"""Owner authority boundaries for invited administrators."""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

import db
from wireless import security as security_module
from wireless.commissioning import CommissioningStore
from wireless.config import DeviceConfig
from wireless.security import SecurityError, SecurityStore


class OpenWindow:
    window = "pairing"

    def require_window(self, kind):
        if self.window != kind:
            raise SecurityError("window_required", 409, "window required")

    def close_windows(self):
        self.window = None


class OwnerProtectionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.enterContext(
            mock.patch.object(db, "DB_PATH", str(Path(self.temp_dir.name) / "test.db"))
        )
        self.enterContext(mock.patch.object(security_module, "PIN_ITERATIONS", 1_000))
        db.init_db()
        self.security = SecurityStore(db.get_conn, b"o" * 32)
        self.security.ensure_schema()
        self.events = OpenWindow()
        config = DeviceConfig(2, str(uuid.uuid4()), "a" * 64, "Test")
        self.commissioning = CommissioningStore(self.security, config, self.events)
        self.commissioning.ensure_schema()
        self.owner_token = "1" * 64
        claim = self.commissioning.claim(
            {
                "request_id": str(uuid.uuid4()),
                "activation_secret": self.commissioning.activation_material(),
                "name": "Owner",
                "pin": "123456",
                "device_name": "Owner phone",
                "device_token": self.owner_token,
                "recovery_secret": "2" * 64,
            }
        )
        self.owner_id = claim["user"]["id"]
        self.owner = self.security.principal(self.owner_token)
        invited = self.security.create_user(
            self.owner, "Invited admin", "654321", is_admin=True
        )
        invite = self.security.issue_invite(self.owner, invited["id"])
        paired = self.security.pair(invite["invite_token"], "654321", "Admin phone")
        self.admin_token = paired["device_token"]
        self.admin = self.security.principal(self.admin_token)

    def test_invited_admin_cannot_change_owner_pin_or_revoke_owner_device(self):
        with self.assertRaises(SecurityError) as pin:
            self.security.set_pin(self.admin, self.owner_id, "111111")
        self.assertEqual(pin.exception.code, "owner_required")
        with self.assertRaises(SecurityError) as device:
            self.security.revoke_device(self.admin, self.owner.device_id)
        self.assertEqual(device.exception.code, "owner_required")
        with self.assertRaises(SecurityError) as role:
            self.security.create_user(
                self.admin, "Unauthorized admin", "111111", is_admin=True
            )
        self.assertEqual(role.exception.code, "owner_required")
        self.assertTrue(self.security.principal(self.owner_token).is_owner)

    def test_owner_target_grants_are_blocked_at_issue_and_consume(self):
        for action in ("user.delete", "user.access", "enrollment.start", "user.pin"):
            with self.subTest(action=action):
                with self.assertRaises(SecurityError) as blocked:
                    self.security.authorize(
                        self.admin,
                        "654321",
                        action,
                        self.owner_id,
                    )
                self.assertEqual(blocked.exception.code, "owner_required")

        with self.security.get_connection() as conn:
            now = self.security._now()
            token = "3" * 64
            conn.execute(
                "INSERT INTO operation_grants "
                "(token_hash,device_id,user_id,action,target,auth_version,"
                "expires_at,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    security_module.token_hash(token),
                    self.admin.device_id,
                    self.admin.user_id,
                    "user.delete",
                    self.owner_id,
                    self.admin.auth_version,
                    now + 30,
                    now,
                ),
            )
        with self.assertRaises(SecurityError) as consume:
            self.security.consume_grant(
                self.admin, token, "user.delete", self.owner_id
            )
        self.assertEqual(consume.exception.code, "owner_required")

    def test_database_guards_owner_delete_disable_and_demotion(self):
        statements = (
            ("UPDATE users SET active=0 WHERE id=?",),
            ("DELETE FROM users WHERE id=?",),
            ("UPDATE user_security SET is_admin=0 WHERE user_id=?",),
            ("DELETE FROM user_security WHERE user_id=?",),
        )
        for (statement,) in statements:
            with self.subTest(statement=statement):
                with self.assertRaises(db.sqlite3.IntegrityError):
                    with db.get_conn() as conn:
                        conn.execute("PRAGMA foreign_keys=ON")
                        conn.execute(statement, (self.owner_id,))

    def test_resumable_pair_uses_precreated_token_and_exact_receipt(self):
        user = self.security.create_user(self.owner, "User", "333333")
        invite = self.security.issue_invite(self.owner, user["id"])
        request_id = str(uuid.uuid4())
        device_token = "4" * 64
        paired = self.security.pair(
            invite["invite_token"],
            "333333",
            "Prepared phone",
            request_id=request_id,
            device_token=device_token,
        )
        self.assertNotIn("device_token", paired)
        self.assertEqual(paired["request_id"], request_id)
        self.assertEqual(
            self.security.principal(device_token).user_id,
            user["id"],
        )
        self.assertEqual(
            self.security.pair(
                invite["invite_token"],
                "333333",
                "Prepared phone",
                request_id=request_id,
                device_token=device_token,
            ),
            paired,
        )
        with self.assertRaises(SecurityError) as changed:
            self.security.pair(
                invite["invite_token"],
                "333333",
                "Changed phone",
                request_id=request_id,
                device_token=device_token,
            )
        self.assertEqual(changed.exception.code, "request_id_reused")

    def test_transfer_pin_failures_persist_and_lock(self):
        self.events.window = "pairing"
        with mock.patch("wireless.security.time.time", return_value=1_000):
            for _ in range(4):
                with self.assertRaises(SecurityError) as wrong:
                    self.commissioning.start_transfer(
                        self.owner,
                        {
                            "request_id": str(uuid.uuid4()),
                            "pin": "000000",
                            "transfer_secret": "5" * 64,
                        },
                    )
                self.assertEqual(wrong.exception.code, "invalid_credentials")
            with self.assertRaises(SecurityError) as locked:
                self.commissioning.start_transfer(
                    self.owner,
                    {
                        "request_id": str(uuid.uuid4()),
                        "pin": "000000",
                        "transfer_secret": "6" * 64,
                    },
                )
        self.assertEqual(locked.exception.code, "pin_locked")

    def test_pending_transfer_is_invalidated_by_owner_pin_change(self):
        self.events.window = "pairing"
        transfer_secret = "7" * 64
        self.commissioning.start_transfer(
            self.owner,
            {
                "request_id": str(uuid.uuid4()),
                "pin": "123456",
                "transfer_secret": transfer_secret,
            },
        )
        self.security.set_pin(self.owner, self.owner_id, "222222")
        accept = {
            "request_id": str(uuid.uuid4()),
            "transfer_secret": transfer_secret,
            "name": "Successor",
            "pin": "333333",
            "device_name": "Successor phone",
            "device_token": "8" * 64,
            "recovery_secret": "9" * 64,
        }
        with self.assertRaises(SecurityError) as invalid:
            self.commissioning.validate_transfer_accept(accept)
        self.assertEqual(invalid.exception.code, "invalid_transfer")


if __name__ == "__main__":
    unittest.main()
