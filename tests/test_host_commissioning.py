"""Exercise the composed product/developer boundary with isolated SQLite data."""

from dataclasses import replace
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest import mock
import uuid

import db
import db_api
from bass_wireless import main
from pi_device_api import create_app
from security_fixtures import AuthorizedFakeRuntime
from wireless.api import secure_wireless_app
from wireless.config import load_or_create_device_config
from wireless.developer_reset import assert_reset_complete
from wireless.host_lifecycle import HostLifecycle
from wireless.security import SecurityStore


class LifecycleRuntime(AuthorizedFakeRuntime):
    def quiesce(self, reason="developer_reset", timeout=10):
        self.calls.append(("quiesce", reason))
        return {"ok": True, "maintenance": True}

    def resume_after_maintenance(self):
        self.calls.append(("resume",))


class HostCommissioningTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(db, "DB_PATH", str(self.root / "faceid.db")))
        self.enterContext(mock.patch("wireless.security.PIN_ITERATIONS", 1000))
        db.init_db()
        self.config_path = self.root / "device.json"
        self.config = replace(
            load_or_create_device_config(self.config_path),
            tls_certificate_sha256="a" * 64,
        )
        self.config_path.with_suffix(".tls.pem").write_bytes(b"isolated TLS fixture")
        self.config_path.with_suffix(".auth.key").write_bytes(b"s" * 32)
        self.security = SecurityStore(db.get_conn, b"s" * 32)
        self.security.ensure_schema()
        self.runtime = LifecycleRuntime(db_api)
        self.lifecycle = HostLifecycle(self.security, self.config, self.runtime)
        self.now = 100.0
        self.lifecycle.events._clock = lambda: self.now
        self.origin = "http://localhost:5057"
        self.https = "https://device.local:5056"
        self.headers = {"Origin": self.origin, "Referer": self.origin + "/"}
        self.client = None

    def build(self, developer=False, mode="pc"):
        if developer:
            self.lifecycle.enable_developer_controls(self.config_path, 5057)
        app = secure_wireless_app(
            create_app(db_module=db_api, runtime=self.runtime),
            config=self.config, mode=mode, port=5057, dist_root=self.root,
            security=self.security, commissioning=self.lifecycle.commissioning,
            events=self.lifecycle.events,
            developer_control=self.lifecycle.developer_control,
            transfer_handler=self.lifecycle.accept_transfer,
            recovery_handler=self.lifecycle.recover_owner,
        )
        self.app = app
        self.client = app.test_client()
        return app

    def developer_session(self):
        response = self.client.get(
            "/local/hardware/status",
            base_url=self.origin,
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["authenticated"])
        self.headers["X-BASS-Dev-CSRF"] = response.json["csrf_token"]
        return response.json["csrf_token"]

    def local_post(self, path, body):
        return self.client.post(
            f"/local/hardware/{path}", base_url=self.origin,
            headers=self.headers, json=body,
        )

    def post(self, path, body, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.post(path, base_url=self.https, json=body, headers=headers)

    def hold(self, seconds=3):
        press_id = str(uuid.uuid4())
        self.lifecycle.events.press_down(press_id)
        for _ in range(seconds):
            self.now += 1
            self.lifecycle.events.press_keepalive(press_id)
        self.lifecycle.events.press_up(press_id)

    def claim_body(self):
        return {
            "request_id": str(uuid.uuid4()),
            "activation_secret": self.lifecycle.commissioning.activation_material(),
            "name": "Owner", "pin": "123456", "device_name": "Owner phone",
            "device_token": secrets.token_hex(32),
            "recovery_secret": secrets.token_hex(32),
        }

    def claim(self):
        body = self.claim_body()
        self.hold()
        response = self.post("/api/commissioning/claim", body)
        self.assertEqual(response.status_code, 201, response.json)
        return body, response.json

    def test_default_and_pi_modes_have_no_developer_routes(self):
        for mode in ("pc", "pi"):
            self.build(mode=mode)
            response = self.client.get(
                "/local/hardware/status?enabled=true", base_url=self.origin,
                headers=self.headers,
            )
            self.assertEqual(response.status_code, 404)
            self.assertEqual(self.local_post("reset", {
                "request_id": str(uuid.uuid4()), "confirmation": "RESET",
            }).status_code, 404)
        with self.assertRaisesRegex(ValueError, "PC development mode"):
            main(["--mode", "pi", "--hardware-simulator"])
        with self.assertRaisesRegex(ValueError, "offline commands"):
            main(["--mode", "pc", "--hardware-simulator", "--migrate-security-only"])
        self.lifecycle.enable_developer_controls(self.config_path, 5057)
        with self.assertRaisesRegex(ValueError, "PC development mode"):
            self.build(mode="pi")

    def test_developer_entry_auto_sessions_localhost_and_requires_csrf(self):
        self.build(developer=True)
        csrf = self.developer_session()
        self.assertEqual(
            self.local_post("session", {}).status_code,
            404,
        )
        no_cookie_client = self.app.test_client()
        no_cookie = no_cookie_client.post(
            "/local/hardware/power",
            base_url=self.origin,
            headers={**self.headers, "X-BASS-Dev-CSRF": csrf},
            json={"powered": False},
        )
        self.assertEqual(no_cookie.status_code, 401)
        for overrides in (
            {"REMOTE_ADDR": "192.168.1.8"},
            {"SERVER_PORT": "5056", "wsgi.url_scheme": "https"},
        ):
            response = self.client.post(
                "/local/hardware/power", base_url=self.origin, headers=self.headers,
                json={"powered": False}, environ_overrides=overrides,
            )
            self.assertEqual(response.status_code, 403)
        no_csrf = {key: value for key, value in self.headers.items()
                   if key != "X-BASS-Dev-CSRF"}
        response = self.client.post("/local/hardware/power", base_url=self.origin,
                                    headers=no_csrf, json={"powered": False})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(self.lifecycle.events.snapshot()["powered"])

    def test_first_owner_requires_tls_activation_and_window_and_retry_is_exact(self):
        self.build()
        body = self.claim_body()
        response = self.client.post("/api/commissioning/claim", json=body)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.post("/api/commissioning/claim", body).status_code, 409)
        self.hold()
        bad = {**body, "activation_secret": secrets.token_hex(32)}
        self.assertEqual(self.post("/api/commissioning/claim", bad).status_code, 401)
        claimed = self.post("/api/commissioning/claim", body)
        self.assertEqual(claimed.status_code, 201, claimed.json)
        self.assertTrue(claimed.json["user"]["is_owner"])
        self.assertEqual(self.post("/api/commissioning/claim", body).json, claimed.json)
        altered = {**body, "name": "Changed"}
        self.assertEqual(self.post("/api/commissioning/claim", altered).status_code, 409)
        old_local_setup = self.client.post(
            "/local/security/initial-admin", base_url=self.origin,
            headers=self.headers, json={"name": "Other", "pin": "654321"},
        )
        self.assertEqual(old_local_setup.status_code, 410)
        self.assertEqual(len(db_api.get_all_users()), 1)

    def test_power_keeps_owner_and_reset_replay_keeps_subsequent_claim(self):
        self.build(developer=True)
        self.developer_session()
        old, old_result = self.claim()
        self.assertEqual(self.local_post("power", {"powered": False}).status_code, 200)
        blocked = self.client.get("/api/status", base_url=self.https,
            headers={"Authorization": f"Bearer {old['device_token']}"})
        self.assertEqual(blocked.status_code, 503)
        self.assertEqual(self.lifecycle.commissioning.owner_user_id(), old_result["user"]["id"])
        self.assertEqual(self.local_post("power", {"powered": True}).status_code, 200)
        reset = {"request_id": str(uuid.uuid4()), "confirmation": "RESET"}
        response = self.local_post("reset", reset)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.lifecycle.commissioning.status()["ownership"], "unclaimed")
        self.assertEqual(self.client.get("/api/me", base_url=self.https,
            headers={"Authorization": f"Bearer {old['device_token']}"}).status_code, 401)
        self.hold()
        self.assertEqual(self.post("/api/commissioning/claim", old).status_code, 401)
        newer, newer_result = self.claim()
        replay = self.local_post("reset", reset)
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertEqual(replay.json["receipt"], response.json["receipt"])
        self.assertEqual(self.lifecycle.commissioning.owner_user_id(), newer_result["user"]["id"])
        self.assertEqual(self.client.get("/api/me", base_url=self.https,
            headers={"Authorization": f"Bearer {newer['device_token']}"}).status_code, 200)

    def test_bad_transfer_cannot_pause_product_and_commit_drains_old_work(self):
        self.build()
        owner, _ = self.claim()
        successor = {**self.claim_body(), "name": "Successor",
                     "transfer_secret": secrets.token_hex(32)}
        successor.pop("activation_secret")
        invalid = self.post("/api/commissioning/transfer/accept", successor)
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(self.runtime.calls, [])
        self.hold()
        transfer = self.post("/api/ownership/transfer", {
            "request_id": str(uuid.uuid4()), "pin": owner["pin"],
            "transfer_secret": successor["transfer_secret"],
        }, owner["device_token"])
        self.assertEqual(transfer.status_code, 201, transfer.json)
        accepted = self.post("/api/commissioning/transfer/accept", successor)
        self.assertEqual(accepted.status_code, 201, accepted.json)
        self.assertEqual(self.runtime.calls, [("quiesce", "ownership_transfer"), ("resume",)])
        repeated = self.post("/api/commissioning/transfer/accept", successor)
        self.assertEqual(repeated.json, accepted.json)
        self.assertEqual(len(self.runtime.calls), 2)
        self.assertFalse(self.lifecycle.events.maintenance)
        self.assertEqual([user["name"] for user in db_api.get_all_users()], ["Successor"])

    def test_backup_failure_keeps_data_and_blocks_normal_restart(self):
        self.build(developer=True)
        self.developer_session()
        _, claimed = self.claim()
        self.config_path.with_suffix(".auth.key").unlink()
        response = self.local_post("reset", {
            "request_id": str(uuid.uuid4()), "confirmation": "RESET",
        })
        self.assertEqual(response.status_code, 503, response.json)
        self.assertEqual(self.lifecycle.commissioning.owner_user_id(), claimed["user"]["id"])
        self.assertTrue(self.lifecycle.events.maintenance)
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            assert_reset_complete(self.config_path)

    def test_recovery_proof_precedes_drain_and_locks_old_operation(self):
        self.build()
        owner, original = self.claim()
        status = self.client.get("/api/commissioning/status", base_url=self.https)
        self.assertEqual(status.status_code, 200)
        self.assertNotIn("owner_user_id", status.json)
        self.assertNotIn("generation", status.json)
        recovery = {
            "request_id": str(uuid.uuid4()), "pin": "654321",
            "device_name": "Recovered phone", "device_token": secrets.token_hex(32),
            "recovery_secret": owner["recovery_secret"],
            "new_recovery_secret": secrets.token_hex(32),
        }
        self.assertEqual(self.post("/api/commissioning/recover", recovery).status_code, 409)
        self.assertEqual(self.runtime.calls, [])
        self.hold(10)
        bad = {**recovery, "recovery_secret": secrets.token_hex(32)}
        self.assertEqual(self.post("/api/commissioning/recover", bad).status_code, 401)
        self.assertEqual(self.runtime.calls, [])
        recovered = self.post("/api/commissioning/recover", recovery)
        self.assertEqual(recovered.status_code, 200, recovered.json)
        self.assertEqual(recovered.json["user"]["id"], original["user"]["id"])
        self.assertEqual(self.runtime.calls, [("quiesce", "owner_recovery"), ("resume",)])
        self.assertEqual(self.post("/api/commissioning/recover", recovery).json, recovered.json)
        self.assertEqual(len(self.runtime.calls), 2)
        self.assertEqual(self.client.get("/api/me", base_url=self.https,
            headers={"Authorization": f"Bearer {owner['device_token']}"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
