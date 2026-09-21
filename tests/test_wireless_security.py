from contextlib import contextmanager
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
from wireless import security
from wireless.security import SecurityError, SecurityStore


class WirelessSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = mock.patch.object(
            db,
            "DB_PATH",
            str(Path(self.temp_dir.name) / "faceid.db"),
        )
        self.iterations_patch = mock.patch.object(security, "PIN_ITERATIONS", 1_000)
        self.db_patch.start()
        self.iterations_patch.start()
        db.init_db()
        self.store = SecurityStore(db.get_conn, b"p" * 32)
        self.store.ensure_schema()

    def tearDown(self):
        self.iterations_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _bootstrap_admin(self):
        user = self.store.initial_admin("Ada", "123456")
        login = self.store.local_login("Ada", "123456")
        principal = self.store.principal(login["device_token"])
        return user, login, principal

    def _create_and_pair_user(self, admin, name="Grace", pin="654321"):
        user = self.store.create_user(admin, name, pin)
        invite = self.store.issue_invite(admin, user["id"])
        paired = self.store.pair(invite["invite_token"], pin, f"{name} phone")
        principal = self.store.principal(paired["device_token"])
        return user, paired, principal

    def _count(self, table):
        with db.get_conn() as conn:
            return conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()[
                "count"
            ]

    def test_security_parameters_and_initial_admin_contract(self):
        self.assertEqual(security.PIN_MAX_FAILURES, 5)
        self.assertEqual(security.PIN_LOCKOUT_SECONDS, 60)
        self.assertFalse(self.store.configured())

        user = self.store.initial_admin(" Ada ", "123456")

        self.assertEqual(user["name"], "Ada")
        self.assertTrue(user["is_admin"])
        self.assertTrue(user["faceAccess"])
        self.assertTrue(self.store.configured())
        with self.assertRaisesRegex(SecurityError, "active administrator"):
            self.store.initial_admin("Other", "123456")

    def test_pairing_requires_admin_invite_correct_pin_and_is_one_time(self):
        _, _, admin = self._bootstrap_admin()
        user = self.store.create_user(admin, "Grace", "654321")
        invite = self.store.issue_invite(admin, user["id"])

        with self.assertRaises(SecurityError) as wrong_pin:
            self.store.pair(invite["invite_token"], "000000", "Grace phone")
        self.assertEqual(wrong_pin.exception.code, "invalid_credentials")

        paired = self.store.pair(invite["invite_token"], "654321", "Grace phone")
        principal = self.store.principal(paired["device_token"])
        self.assertEqual(principal.user_id, user["id"])
        self.assertFalse(principal.is_admin)

        with self.assertRaises(SecurityError) as reused:
            self.store.pair(invite["invite_token"], "654321", "Second phone")
        self.assertEqual(reused.exception.code, "invalid_pairing_invite")

    def test_pending_invite_requires_current_issuer_device_and_version(self):
        admin_user, login, admin = self._bootstrap_admin()
        target = self.store.create_user(admin, "Grace", "654321")
        revoked_invite = self.store.issue_invite(admin, target["id"])

        self.store.revoke_device(admin, login["device_id"])

        with self.assertRaises(SecurityError) as revoked:
            self.store.pair(
                revoked_invite["invite_token"],
                "654321",
                "Revoked issuer",
            )
        self.assertEqual(revoked.exception.code, "invalid_pairing_invite")

        login = self.store.local_login("Ada", "123456")
        admin = self.store.principal(login["device_token"])
        stale_version_invite = self.store.issue_invite(admin, target["id"])
        self.store.set_pin(admin, admin_user["id"], "111111")

        with self.assertRaises(SecurityError) as stale_version:
            self.store.pair(
                stale_version_invite["invite_token"],
                "654321",
                "Stale issuer version",
            )
        self.assertEqual(stale_version.exception.code, "invalid_pairing_invite")

    def test_pairing_invites_and_mobile_device_rows_are_bounded(self):
        _, _, admin = self._bootstrap_admin()
        user = self.store.create_user(admin, "Grace", "654321")
        replaced = self.store.issue_invite(admin, user["id"])
        current = self.store.issue_invite(admin, user["id"])
        with self.assertRaises(SecurityError) as stale_invite:
            self.store.pair(replaced["invite_token"], "654321", "Stale")
        self.assertEqual(stale_invite.exception.code, "invalid_pairing_invite")

        devices = [
            self.store.pair(current["invite_token"], "654321", "Phone 0")
        ]
        for index in range(1, 10):
            invite = self.store.issue_invite(admin, user["id"])
            devices.append(
                self.store.pair(
                    invite["invite_token"],
                    "654321",
                    f"Phone {index}",
                )
            )

        extra_invite = self.store.issue_invite(admin, user["id"])
        with self.assertRaises(SecurityError) as at_limit:
            self.store.pair(extra_invite["invite_token"], "654321", "Too many")
        self.assertEqual(at_limit.exception.code, "device_limit_reached")

        self.store.revoke_device(admin, devices[0]["device_id"])
        replacement = self.store.pair(
            extra_invite["invite_token"],
            "654321",
            "Replacement",
        )
        self.assertIsNotNone(self.store.principal(replacement["device_token"]))
        with db.get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM paired_devices "
                "WHERE user_id=? AND kind='mobile'",
                (user["id"],),
            ).fetchone()["count"]
        self.assertEqual(count, 10)

    def test_non_admin_cannot_issue_invite_or_authorize_admin_action(self):
        _, _, admin = self._bootstrap_admin()
        user, _, principal = self._create_and_pair_user(admin)

        with self.assertRaises(SecurityError) as invite_error:
            self.store.issue_invite(principal, user["id"])
        self.assertEqual(invite_error.exception.code, "admin_required")

        with self.assertRaises(SecurityError) as grant_error:
            self.store.authorize(principal, "654321", "settings.update")
        self.assertEqual(grant_error.exception.code, "admin_required")

    def test_pin_failures_persist_and_lock_for_sixty_seconds(self):
        self.store.initial_admin("Ada", "123456")
        with mock.patch("wireless.security.time.time", return_value=1_000):
            for _ in range(4):
                with self.assertRaises(SecurityError) as wrong:
                    self.store.local_login("Ada", "000000")
                self.assertEqual(wrong.exception.code, "invalid_credentials")

            with self.assertRaises(SecurityError) as locked:
                self.store.local_login("Ada", "000000")
            self.assertEqual(locked.exception.code, "pin_locked")
            self.assertEqual(locked.exception.status, 423)

            with self.assertRaises(SecurityError) as still_locked:
                self.store.local_login("Ada", "123456")
            self.assertEqual(still_locked.exception.code, "pin_locked")

        with mock.patch("wireless.security.time.time", return_value=1_061):
            login = self.store.local_login("Ada", "123456")
            self.assertEqual(login["expires_in"], 900)

    def test_remembered_user_id_selects_one_account_even_with_shared_pin(self):
        admin_user, _, admin = self._bootstrap_admin()
        other = self.store.create_user(admin, "Grace", "123456")
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET name='Renamed Ada' WHERE id=?",
                         (admin_user["id"],))

        login = self.store.local_login(None, "123456", user_id=admin_user["id"])
        self.assertEqual(login["user"]["id"], admin_user["id"])
        self.assertEqual(login["user"]["name"], "Renamed Ada")
        self.assertTrue(login["user"]["is_admin"])
        other_login = self.store.local_login(None, "123456", user_id=other["id"])
        self.assertEqual(other_login["user"]["id"], other["id"])
        self.assertFalse(other_login["user"]["is_admin"])

        for user_id in ("missing", "' OR 1=1 --"):
            with self.subTest(user_id=user_id), self.assertRaises(SecurityError) as error:
                self.store.local_login(None, "123456", user_id=user_id)
            self.assertEqual(error.exception.code, "invalid_credentials")
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET active=0 WHERE id=?", (other["id"],))
        with self.assertRaises(SecurityError) as disabled:
            self.store.local_login(None, "123456", user_id=other["id"])
        self.assertEqual(disabled.exception.code, "invalid_credentials")

    def test_remembered_login_and_phone_entry_share_pin_cooldown(self):
        user, _, admin = self._bootstrap_admin()
        invite = self.store.issue_invite(admin, user["id"])
        paired = self.store.pair(invite["invite_token"], "123456", "Ada phone")
        phone = self.store.principal(paired["device_token"])
        with mock.patch("wireless.security.time.time", return_value=1_000):
            for attempt in range(4):
                with self.assertRaises(SecurityError) as wrong:
                    if attempt % 2:
                        self.store.confirm_session(phone, "000000")
                    else:
                        self.store.local_login(None, "000000", user_id=user["id"])
                self.assertEqual(wrong.exception.code, "invalid_credentials")
            with self.assertRaises(SecurityError) as locked:
                self.store.confirm_session(phone, "000000")
            self.assertEqual(locked.exception.code, "pin_locked")
            with self.assertRaises(SecurityError) as still_locked:
                self.store.local_login(None, "123456", user_id=user["id"])
            self.assertEqual(still_locked.exception.code, "pin_locked")
        with mock.patch("wireless.security.time.time", return_value=1_061):
            self.assertEqual(self.store.confirm_session(phone, "123456"), phone)
        self.assertEqual(self._count("operation_grants"), 0)

    def test_phone_entry_rechecks_revocation_and_auth_version(self):
        _, _, admin = self._bootstrap_admin()
        user, _, phone = self._create_and_pair_user(admin)
        self.store.set_pin(admin, user["id"], "111111")
        with self.assertRaises(SecurityError):
            self.store.confirm_session(phone, "111111")
        invite = self.store.issue_invite(admin, user["id"])
        paired = self.store.pair(invite["invite_token"], "111111", "New phone")
        replacement = self.store.principal(paired["device_token"])
        self.store.revoke_device(admin, replacement.device_id)
        with self.assertRaises(SecurityError):
            self.store.confirm_session(replacement, "111111")

    def test_face_failure_lockout_persists_across_store_reopen(self):
        _, _, admin = self._bootstrap_admin()
        with mock.patch("wireless.security.time.time", return_value=1_000):
            self.store.check_face_attempt(admin)
            self.store.record_face_result(admin, False, 2)
            self.store.check_face_attempt(admin)
            self.store.record_face_result(admin, False, 2)
            with self.assertRaises(SecurityError) as locked:
                self.store.check_face_attempt(admin)
            self.assertEqual(locked.exception.code, "face_locked")
            self.assertEqual(locked.exception.status, 423)

        reopened = SecurityStore(db.get_conn, b"p" * 32)
        with mock.patch("wireless.security.time.time", return_value=1_059):
            with self.assertRaises(SecurityError) as still_locked:
                reopened.check_face_attempt(admin)
            self.assertEqual(still_locked.exception.code, "face_locked")

        with mock.patch("wireless.security.time.time", return_value=1_060):
            reopened.check_face_attempt(admin)
            reopened.record_face_result(admin, False, 2)
            reopened.record_face_result(admin, True, 2)

        with (
            mock.patch("wireless.security.time.time", return_value=1_061),
            mock.patch.object(
                reopened,
                "_audit",
                side_effect=sqlite3.OperationalError("injected audit failure"),
            ),
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, "audit failure"):
                reopened.record_face_result(admin, False, 2)

        with db.get_conn() as conn:
            state = conn.execute(
                "SELECT face_failed_attempts, face_lockout_until "
                "FROM user_security WHERE user_id=?",
                (admin.user_id,),
            ).fetchone()
        self.assertEqual(state["face_failed_attempts"], 0)
        self.assertEqual(state["face_lockout_until"], 0)

    def test_ensure_schema_adds_security_columns_to_existing_tables(self):
        legacy_path = Path(self.temp_dir.name) / "legacy-security.db"

        @contextmanager
        def legacy_conn():
            conn = sqlite3.connect(legacy_path)
            conn.row_factory = sqlite3.Row
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

        with legacy_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    face_encoding BLOB,
                    active INTEGER NOT NULL,
                    face_access INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE auth_logs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    stage TEXT,
                    result TEXT,
                    detail TEXT,
                    ts INTEGER NOT NULL
                );
                CREATE TABLE user_security (
                    user_id TEXT PRIMARY KEY REFERENCES users(id),
                    pin_salt BLOB NOT NULL,
                    pin_verifier BLOB NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    lockout_until INTEGER NOT NULL DEFAULT 0,
                    auth_version INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE pairing_invites (
                    token_hash BLOB PRIMARY KEY,
                    target_user_id TEXT NOT NULL REFERENCES users(id),
                    issued_by_user_id TEXT NOT NULL REFERENCES users(id),
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
                INSERT INTO users VALUES ('issuer', 'Issuer', NULL, 1, 1, 1);
                INSERT INTO users VALUES ('target', 'Target', NULL, 1, 1, 1);
                INSERT INTO user_security VALUES (
                    'issuer', zeroblob(16), zeroblob(32), 1, 0, 0, 1, 1, 1
                );
                INSERT INTO user_security VALUES (
                    'target', zeroblob(16), zeroblob(32), 0, 0, 0, 1, 1, 1
                );
                INSERT INTO pairing_invites VALUES (
                    zeroblob(32), 'target', 'issuer', 9999999999, 1
                );
                """
            )

        legacy_store = SecurityStore(legacy_conn, b"l" * 32)
        legacy_store.ensure_schema()
        legacy_store.ensure_schema()
        with legacy_conn() as conn:
            user_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(user_security)")
            }
            invite_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(pairing_invites)")
            }
            invitation_count = conn.execute(
                "SELECT COUNT(*) FROM pairing_invites"
            ).fetchone()[0]
        self.assertIn("face_failed_attempts", user_columns)
        self.assertIn("face_lockout_until", user_columns)
        self.assertIn("issued_by_device_id", invite_columns)
        self.assertIn("issuer_auth_version", invite_columns)
        self.assertEqual(invitation_count, 0)

    def test_local_login_is_expiring_and_does_not_use_mobile_slots(self):
        user, login, admin = self._bootstrap_admin()
        with db.get_conn() as conn:
            for index in range(10):
                conn.execute(
                    "INSERT INTO paired_devices "
                    "(id, user_id, name, token_hash, kind, active, "
                    "created_at, last_seen) "
                    "VALUES (?,?,?,?, 'mobile',1,?,?)",
                    (
                        f"mobile-{index}",
                        user["id"],
                        f"Phone {index}",
                        bytes([index]) * 32,
                        index,
                        index,
                    ),
                )
        second_login = self.store.local_login("Ada", "123456")
        self.assertIsNotNone(self.store.principal(second_login["device_token"]))
        with self.assertRaises(SecurityError):
            self.store.principal(login["device_token"])

        with db.get_conn() as conn:
            expires_at = conn.execute(
                "SELECT expires_at FROM paired_devices WHERE id=?",
                (second_login["device_id"],),
            ).fetchone()["expires_at"]
        with mock.patch("wireless.security.time.time", return_value=expires_at):
            with self.assertRaises(SecurityError) as expired:
                self.store.principal(second_login["device_token"])
        self.assertEqual(expired.exception.code, "invalid_device_credential")
        self.assertTrue(admin.is_admin)

    def test_local_login_rejects_ambiguous_legacy_name(self):
        user = self.store.initial_admin("Ada", "123456")
        with db.get_conn() as conn:
            security_row = conn.execute(
                "SELECT pin_salt, pin_verifier FROM user_security WHERE user_id=?",
                (user["id"],),
            ).fetchone()
            conn.execute("DROP INDEX idx_users_active_name")
            conn.execute(
                "INSERT INTO users "
                "(id, name, face_encoding, active, face_access, created_at) "
                "VALUES ('duplicate-ada',' Ada ',NULL,1,1,1)"
            )
            conn.execute(
                "INSERT INTO user_security "
                "(user_id, pin_salt, pin_verifier, created_at, updated_at) "
                "VALUES ('duplicate-ada',?,?,1,1)",
                (security_row["pin_salt"], security_row["pin_verifier"]),
            )

        with self.assertRaises(SecurityError) as ambiguous:
            self.store.local_login("Ada", "123456")
        self.assertEqual(ambiguous.exception.code, "invalid_credentials")

    def test_operation_grant_is_bound_one_time_and_versioned(self):
        _, _, admin = self._bootstrap_admin()
        with self.assertRaises(SecurityError) as missing:
            self.store.consume_grant(
                admin,
                "",
                "scan.unlock",
                admin.user_id,
            )
        self.assertEqual(missing.exception.code, "invalid_operation_grant")
        self.assertEqual(missing.exception.status, 403)
        self.assertTrue(self.store.still_authorized(admin))

        grant = self.store.authorize(
            admin,
            "123456",
            "scan.unlock",
            admin.user_id,
        )
        self.store.consume_grant(
            admin,
            grant["grant_token"],
            "scan.unlock",
            admin.user_id,
        )
        with self.assertRaises(SecurityError) as reused:
            self.store.consume_grant(
                admin,
                grant["grant_token"],
                "scan.unlock",
                admin.user_id,
            )
        self.assertEqual(reused.exception.code, "invalid_operation_grant")

        grant = self.store.authorize(
            admin,
            "123456",
            "scan.unlock",
            admin.user_id,
        )
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE users SET face_encoding=? WHERE id=?",
                (b"new template", admin.user_id),
            )
        self.assertFalse(self.store.still_authorized(admin))
        self.assertEqual(self._count("operation_grants"), 0)
        with self.assertRaises(SecurityError) as stale:
            self.store.consume_grant(
                admin,
                grant["grant_token"],
                "scan.unlock",
                admin.user_id,
            )
        self.assertEqual(stale.exception.code, "stale_principal")

    def test_control_grants_require_own_user_but_not_face_access(self):
        _, _, admin = self._bootstrap_admin()
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE users SET face_access=0 WHERE id=?",
                (admin.user_id,),
            )
        refreshed = self.store.principal(
            self.store.local_login("Ada", "123456")["device_token"]
        )

        control = self.store.authorize(
            refreshed,
            "123456",
            "ignition.stop",
            refreshed.user_id,
        )
        self.assertIn("grant_token", control)
        with self.assertRaises(SecurityError) as scan:
            self.store.authorize(
                refreshed,
                "123456",
                "scan.unlock",
                refreshed.user_id,
            )
        self.assertEqual(scan.exception.code, "face_access_denied")

    def test_set_pin_revokes_devices_grants_and_old_pin(self):
        _, _, admin = self._bootstrap_admin()
        user, paired, principal = self._create_and_pair_user(admin)
        self.store.authorize(
            principal,
            "654321",
            "scan.unlock",
            user["id"],
        )

        self.store.set_pin(admin, user["id"], "111111")

        with self.assertRaises(SecurityError):
            self.store.principal(paired["device_token"])
        self.assertFalse(self.store.still_authorized(principal))
        self.assertEqual(self._count("operation_grants"), 0)
        invite = self.store.issue_invite(admin, user["id"])
        with self.assertRaises(SecurityError):
            self.store.pair(invite["invite_token"], "654321", "Old PIN phone")
        paired_again = self.store.pair(
            invite["invite_token"],
            "111111",
            "New PIN phone",
        )
        principal = self.store.principal(paired_again["device_token"])
        self.assertEqual(principal.user_id, user["id"])

    def test_set_pin_claims_active_legacy_user_without_security_row(self):
        _, _, admin = self._bootstrap_admin()
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO users "
                "(id, name, face_encoding, active, face_access, created_at) "
                "VALUES ('legacy-user','Legacy',NULL,1,1,1)"
            )

        self.store.set_pin(admin, "legacy-user", "222222")

        user = self.store.get_user("legacy-user")
        self.assertIsNotNone(user)
        self.assertFalse(user["is_admin"])
        invite = self.store.issue_invite(admin, "legacy-user")
        paired = self.store.pair(invite["invite_token"], "222222", "Legacy phone")
        self.assertEqual(
            self.store.principal(paired["device_token"]).user_id,
            "legacy-user",
        )

    def test_offline_admin_recovery_promotes_user_and_revokes_credentials(self):
        _, _, admin = self._bootstrap_admin()
        user, paired, _ = self._create_and_pair_user(admin)

        recovered = self.store.recover_admin("Grace", "111111")

        self.assertEqual(recovered["id"], user["id"])
        self.assertTrue(recovered["is_admin"])
        with self.assertRaises(SecurityError):
            self.store.principal(paired["device_token"])
        with self.assertRaises(SecurityError):
            self.store.local_login("Grace", "654321")
        login = self.store.local_login("Grace", "111111")
        self.assertTrue(self.store.principal(login["device_token"]).is_admin)

    def test_empty_target_normalizes_to_targetless_admin_grant(self):
        _, _, admin = self._bootstrap_admin()
        grant = self.store.authorize(admin, "123456", "settings.update", "")

        self.store.consume_grant(
            admin,
            grant["grant_token"],
            "settings.update",
            "",
        )
        self.assertEqual(self._count("operation_grants"), 0)

    def test_last_active_admin_cannot_be_disabled_deleted_or_demoted(self):
        user, _, _ = self._bootstrap_admin()
        statements = (
            ("UPDATE users SET active=0 WHERE id=?",),
            ("DELETE FROM users WHERE id=?",),
            ("UPDATE user_security SET is_admin=0 WHERE user_id=?",),
            ("DELETE FROM user_security WHERE user_id=?",),
        )
        for (statement,) in statements:
            with self.subTest(statement=statement):
                with self.assertRaises(sqlite3.IntegrityError):
                    with db.get_conn() as conn:
                        conn.execute(statement, (user["id"],))

    def test_audit_failure_rolls_back_security_mutation(self):
        _, _, admin = self._bootstrap_admin()
        with mock.patch.object(
            self.store,
            "_audit",
            side_effect=sqlite3.OperationalError("injected audit failure"),
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, "audit failure"):
                self.store.create_user(admin, "Rollback", "222222")

        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE name='Rollback'",
            ).fetchone()
        self.assertIsNone(row)

    def test_device_list_and_logs_never_expose_tokens_or_pin(self):
        _, _, admin = self._bootstrap_admin()
        _, paired, _ = self._create_and_pair_user(admin)

        devices = self.store.list_devices(admin)
        serialized = repr(devices)
        self.assertNotIn(paired["device_token"], serialized)
        self.assertNotIn("654321", serialized)
        with db.get_conn() as conn:
            details = "\n".join(
                row["detail"] or ""
                for row in conn.execute("SELECT detail FROM auth_logs")
            )
        self.assertNotIn(paired["device_token"], details)
        self.assertNotIn("654321", details)


if __name__ == "__main__":
    unittest.main()
