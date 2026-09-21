"""Isolated commissioning lifecycle tests; never use the product database."""

from __future__ import annotations

import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

import db
from wireless import security as security_module
from wireless.commissioning import CommissioningStore
from wireless.config import DeviceConfig
from wireless.security import SecurityError, SecurityStore


class FakeEvents:
    def __init__(self):
        self.window: str | None = None
        self.powered = True
        self.maintenance = False

    def require_window(self, kind: str) -> None:
        if not self.powered or self.window != kind:
            raise SecurityError("window_required", 409, f"{kind} window is required")

    def close_windows(self) -> None:
        self.window = None


class CommissioningTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        db_path = Path(self.temp_dir.name) / "faceid.db"
        self.enterContext(mock.patch.object(db, "DB_PATH", str(db_path)))
        self.enterContext(mock.patch.object(security_module, "PIN_ITERATIONS", 1_000))
        db.init_db()
        self.security = SecurityStore(db.get_conn, b"c" * 32)
        self.security.ensure_schema()
        self.events = FakeEvents()
        self.config = DeviceConfig(2, str(uuid.uuid4()), "a" * 64, "Test")
        self.store = CommissioningStore(self.security, self.config, self.events)
        self.store.ensure_schema()

    @staticmethod
    def _secret(character: str) -> str:
        return character * 64

    def _claim_body(self, **updates):
        body = {
            "request_id": str(uuid.uuid4()),
            "activation_secret": self.store.activation_material(),
            "name": "Owner",
            "pin": "123456",
            "device_name": "Owner phone",
            "device_token": self._secret("1"),
            "recovery_secret": self._secret("2"),
        }
        body.update(updates)
        return body

    def _claim(self):
        body = self._claim_body()
        self.events.window = "pairing"
        return body, self.store.claim(body)

    def test_claim_requires_secret_and_window_and_replays_exact_result(self):
        self.assertEqual(self.store.status()["ownership"], "unclaimed")
        body = self._claim_body()

        with self.assertRaises(SecurityError) as no_window:
            self.store.claim(body)
        self.assertEqual(no_window.exception.code, "window_required")

        self.events.window = "pairing"
        with self.assertRaises(SecurityError) as wrong_secret:
            self.store.claim({**body, "activation_secret": self._secret("f")})
        self.assertEqual(wrong_secret.exception.code, "invalid_activation")

        result = self.store.claim(body)
        self.assertTrue(result["user"]["is_owner"])
        self.assertIsNone(self.store.activation_material())
        self.assertEqual(self.events.window, None)
        self.assertEqual(self.store.claim(body), result)
        with self.assertRaises(SecurityError) as changed_retry:
            self.store.claim({**body, "device_name": "Different phone"})
        self.assertEqual(changed_retry.exception.code, "request_id_reused")

        principal = self.security.principal(body["device_token"])
        self.assertTrue(principal.is_owner)
        self.assertEqual(principal.user_id, result["user"]["id"])

    def test_two_claims_cannot_create_two_owners(self):
        activation = self.store.activation_material()
        first = self._claim_body(
            request_id=str(uuid.uuid4()),
            activation_secret=activation,
            device_token=self._secret("3"),
            recovery_secret=self._secret("4"),
        )
        second = self._claim_body(
            request_id=str(uuid.uuid4()),
            activation_secret=activation,
            name="Other",
            device_token=self._secret("5"),
            recovery_secret=self._secret("6"),
        )
        self.events.window = "pairing"
        barrier = threading.Barrier(3)
        outcomes = []

        def run(body):
            barrier.wait()
            try:
                outcomes.append(("ok", self.store.claim(body)))
            except SecurityError as error:
                outcomes.append(("error", error.code))

        threads = [threading.Thread(target=run, args=(body,)) for body in (first, second)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual([kind for kind, _ in outcomes].count("ok"), 1)
        self.assertEqual([kind for kind, _ in outcomes].count("error"), 1)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_recovery_rotates_proof_and_revokes_old_devices(self):
        claim_body, claim = self._claim()
        recovery_body = {
            "request_id": str(uuid.uuid4()),
            "recovery_secret": claim_body["recovery_secret"],
            "new_recovery_secret": self._secret("7"),
            "pin": "654321",
            "device_name": "Recovered phone",
            "device_token": self._secret("8"),
        }
        self.events.window = "recovery"
        recovered = self.store.recover(recovery_body)
        self.assertEqual(recovered["user"]["id"], claim["user"]["id"])
        with self.assertRaises(SecurityError):
            self.security.principal(claim_body["device_token"])
        self.assertTrue(self.security.principal(recovery_body["device_token"]).is_owner)
        self.assertEqual(self.store.recover(recovery_body), recovered)

        stale = {
            **recovery_body,
            "request_id": str(uuid.uuid4()),
            "new_recovery_secret": self._secret("9"),
            "device_token": self._secret("a"),
        }
        self.events.window = "recovery"
        with self.assertRaises(SecurityError) as old_proof:
            self.store.recover(stale)
        self.assertEqual(old_proof.exception.code, "invalid_recovery")

    def test_recovery_preflight_survives_maintenance_window_close(self):
        claim_body, _ = self._claim()
        recovery_body = {
            "request_id": str(uuid.uuid4()),
            "recovery_secret": claim_body["recovery_secret"],
            "new_recovery_secret": self._secret("7"),
            "pin": "654321",
            "device_name": "Recovered phone",
            "device_token": self._secret("8"),
        }
        with self.assertRaises(SecurityError) as no_window:
            self.store.validate_recovery(recovery_body)
        self.assertEqual(no_window.exception.code, "window_required")

        self.events.window = "recovery"
        validated = self.store.validate_recovery(recovery_body)
        self.assertFalse(validated["completed"])
        with self.assertRaises(SecurityError) as bypass:
            self.store.recover(recovery_body, window_verified=True)
        self.assertEqual(bypass.exception.code, "recovery_preflight_required")

        self.events.maintenance = True
        self.events.close_windows()
        recovered = self.store.recover(recovery_body, window_verified=True)
        self.events.maintenance = False
        replay = self.store.validate_recovery(recovery_body)
        self.assertTrue(replay["completed"])
        self.assertEqual(replay["response"], recovered)

    def test_transfer_wipes_old_identities_and_replays(self):
        claim_body, claimed = self._claim()
        owner = self.security.principal(claim_body["device_token"])
        transfer_secret = self._secret("b")
        start_body = {
            "request_id": str(uuid.uuid4()),
            "pin": "123456",
            "transfer_secret": transfer_secret,
        }
        self.events.window = "pairing"
        self.store.start_transfer(owner, start_body)

        accept_body = {
            "request_id": str(uuid.uuid4()),
            "transfer_secret": transfer_secret,
            "name": "New Owner",
            "pin": "222222",
            "device_name": "Successor phone",
            "device_token": self._secret("c"),
            "recovery_secret": self._secret("d"),
        }
        preflight = self.store.validate_transfer_accept(accept_body)
        self.assertFalse(preflight["completed"])
        accepted = self.store.accept_transfer(accept_body)
        self.assertEqual(accepted["generation"], claimed["generation"] + 1)
        self.assertEqual(self.store.accept_transfer(accept_body), accepted)
        self.assertTrue(
            self.store.validate_transfer_accept(accept_body)["completed"]
        )
        with self.assertRaises(SecurityError):
            self.security.principal(claim_body["device_token"])
        successor = self.security.principal(accept_body["device_token"])
        self.assertTrue(successor.is_owner)
        with db.get_conn() as conn:
            users = conn.execute("SELECT id,name FROM users").fetchall()
        self.assertEqual([(row["id"], row["name"]) for row in users], [
            (successor.user_id, "New Owner")
        ])

    def test_reset_product_returns_unclaimed_and_invalidates_old_credential(self):
        claim_body, claimed = self._claim()
        new_activation = self._secret("e")
        with self.security.synchronized(), self.security.get_connection() as conn:
            self.security._begin(conn)
            result = self.store.reset_product(conn, new_activation)
        self.assertEqual(result["generation"], claimed["generation"] + 1)
        self.assertEqual(self.store.activation_material(), new_activation)
        with self.assertRaises(SecurityError):
            self.security.principal(claim_body["device_token"])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM auth_logs").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
            liveness = conn.execute(
                "SELECT value FROM settings WHERE key='liveness_detection'"
            ).fetchone()["value"]
        self.assertEqual(liveness, "true")

    def test_preexisting_install_is_legacy_and_cannot_reopen_claim(self):
        legacy_dir = tempfile.TemporaryDirectory()
        self.addCleanup(legacy_dir.cleanup)
        legacy_path = Path(legacy_dir.name) / "legacy.db"
        with mock.patch.object(db, "DB_PATH", str(legacy_path)):
            db.init_db()
            security = SecurityStore(db.get_conn, b"l" * 32)
            security.ensure_schema()
            security.initial_admin("Legacy", "123456")
            store = CommissioningStore(security, self.config, FakeEvents())
            store.ensure_schema()
            self.assertEqual(store.status()["ownership"], "legacy")
            self.assertIsNone(store.activation_material())
            with self.assertRaises(SecurityError) as blocked:
                security.initial_admin("Other", "123456")
            self.assertEqual(blocked.exception.code, "admin_already_configured")


if __name__ == "__main__":
    unittest.main()
