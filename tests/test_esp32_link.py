"""Exercise the real serial reader and grant path without camera/motor hardware."""

from __future__ import annotations

import queue
import threading
import time
import unittest
from unittest.mock import patch

from car_face_auth.src.esp32_link import AuthRequest, Esp32Link, parse_line
from car_face_auth.src.pi_runtime import PiRuntime
from tests.test_runtime_safety import FakeCapture, FakeDb


REQUEST_ID = "A1B2C3D400000001"
OTHER_ID = "A1B2C3D400000002"


class SerialProbe:
    def __init__(self):
        self.incoming = queue.Queue()
        self.writes = []
        self.is_open = True
        self.reply = "AUTH_ACK"
        self.reply_id = REQUEST_ID
        self.authorization_sent = threading.Event()
        self.fail_commands = set()

    def readline(self):
        try:
            value = self.incoming.get(timeout=0.02)
        except queue.Empty:
            return b""
        if isinstance(value, Exception):
            raise value
        return value

    def write(self, payload):
        if not self.is_open:
            raise OSError("serial disconnected")
        self.writes.append(payload)
        if payload in self.fail_commands:
            raise OSError("command failed")
        if payload.startswith(b"AUTHORIZED "):
            self.authorization_sent.set()
            if self.reply:
                self.incoming.put(f"{self.reply} {self.reply_id}\n".encode("ascii"))
        return len(payload)

    def flush(self):
        pass

    def close(self):
        self.is_open = False


class LinkRuntime(PiRuntime):
    def __init__(self, db, serial):
        super().__init__(db)
        self.connection = serial
        self.synchronized = threading.Event()
        self.scan_started = threading.Event()

    def _ensure_serial(self):
        if not self.connection.is_open:
            raise OSError("serial unavailable")
        self._serial = self.connection
        return self.connection

    def sync_esp32_state(self):
        super().sync_esp32_state()
        self.synchronized.set()

    def _spawn(self, session_id, target):
        self.scan_started.set()

    def _schedule_session_cleanup(self, session_id):
        pass

    def _close_camera(self, owner_id=None):
        return True


class Esp32LinkTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        self.serial = SerialProbe()
        self.runtime = LinkRuntime(self.db, self.serial)
        self.link = Esp32Link(
            self.runtime, retry_seconds=0.01, max_retry_seconds=0.02,
            ack_timeout=0.1,
        )
        self.runtime._esp32_link = self.link
        self.link.start()
        self.assertTrue(self.runtime.synchronized.wait(2))
        self.serial.writes.clear()

    def tearDown(self):
        self.runtime.close()

    def start_scan(self, purpose="UNLOCK", request_id=REQUEST_ID):
        self.runtime.scan_started.clear()
        self.serial.incoming.put(f"START_AUTH {purpose} {request_id}\n".encode("ascii"))
        self.assertTrue(self.runtime.scan_started.wait(2))
        return self.runtime._session(self.runtime._active_session_id)

    def grant(self, session, candidate="Ada"):
        capture = FakeCapture(session["id"])
        self.runtime._camera_capture = capture
        self.runtime._grant_scan(
            session["id"], candidate, 0.95, session["cancel_event"],
            capture, time.monotonic() + 5,
        )

    def wait_until(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(predicate())

    def grant_in_background(self, session):
        errors = []

        def run():
            try:
                self.grant(session)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        return worker, errors

    def test_only_complete_correlated_protocol_lines_are_accepted(self):
        self.assertEqual(
            parse_line(f"START_AUTH UNLOCK {REQUEST_ID}\n".encode("ascii")),
            ("START_AUTH", REQUEST_ID, "unlock"),
        )
        self.assertEqual(
            parse_line(f"AUTH_CANCEL {REQUEST_ID}\n".encode("ascii")),
            ("AUTH_CANCEL", REQUEST_ID, None),
        )
        for raw in (
            b"START_AUTH UNLOCK\n", b"AUTHORIZED\n", b"START_AUTH LOCK 1234\n",
            f"START_AUTH UNLOCK {REQUEST_ID}".encode("ascii"),
            f"AUTH_ACK {REQUEST_ID} EXTRA\n".encode("ascii"),
            b"AUTH_ACK GGGGGGGGGGGGGGGG\n", b"\xff\n",
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_line(raw))

    def test_matching_ack_commits_one_unlock_without_claiming_physical_feedback(self):
        session = self.start_scan()
        self.grant(session)
        self.assertEqual(session["state"], "granted")
        self.assertEqual(self.db.unlock_writes, 1)
        self.assertEqual(self.db.lock_state, "unlocked")
        self.assertFalse(session["physical_state_confirmed"])
        self.assertEqual(
            self.serial.writes,
            [f"AUTH_PENDING {REQUEST_ID}\n".encode("ascii"),
             f"AUTHORIZED {REQUEST_ID}\n".encode("ascii")],
        )

    def test_missing_ack_never_persists_unlock_and_attempts_stop_lock(self):
        self.serial.reply = None
        session = self.start_scan()
        self.grant(session)
        self.assertEqual(session["state"], "error")
        self.assertEqual(self.db.unlock_writes, 0)
        self.assertEqual(self.db.lock_state, "locked")
        self.assertIsNone(self.runtime._actuation.snapshot().unlock_owner)
        self.assertIn(b"STOP\n", self.serial.writes)
        self.assertIn(b"LOCK\n", self.serial.writes)

    def test_ack_for_another_request_cannot_grant_current_scan(self):
        self.serial.reply_id = OTHER_ID
        session = self.start_scan()
        self.grant(session)
        self.assertEqual(session["state"], "error")
        self.assertEqual(self.db.unlock_writes, 0)

    def test_ignition_uses_matching_ack_and_accepts_another_authorized_driver(self):
        unlocked = self.start_scan()
        self.grant(unlocked)
        self.runtime._release_session(unlocked["id"])
        self.serial.reply_id = OTHER_ID
        ignition = self.start_scan("IGNITION", OTHER_ID)
        self.grant(ignition, "Bob")
        self.assertEqual(ignition["state"], "granted")
        self.assertTrue(self.runtime._actuation.snapshot().ignition_on)
        ignition_log = next(
            row for row in self.db.logs
            if row[0] == "ignition" and row[2].startswith("start:")
        )
        self.assertEqual(ignition_log[3], "u2")
        self.assertIn(f"AUTHORIZED {OTHER_ID}\n".encode("ascii"), self.serial.writes)

    def test_missing_ignition_ack_and_failed_compensation_invalidate_unlock(self):
        unlocked = self.start_scan()
        self.grant(unlocked)
        self.runtime._release_session(unlocked["id"])
        self.serial.reply = None
        self.serial.fail_commands = {b"STOP\n", b"LOCK\n"}
        ignition = self.start_scan("IGNITION", OTHER_ID)
        self.grant(ignition, "Bob")
        snapshot = self.runtime._actuation.snapshot()
        self.assertEqual(ignition["state"], "error")
        self.assertFalse(snapshot.ignition_on)
        self.assertIsNone(snapshot.unlock_owner)
        self.assertFalse(snapshot.auto_relock_armed)
        self.assertFalse(snapshot.ignition_stop_armed)
        self.assertFalse(self.runtime.set_ignition(True)["ok"])

    def test_expiry_cancels_host_scan_before_late_face_match(self):
        session = self.start_scan()
        self.serial.incoming.put(f"AUTH_CANCEL {REQUEST_ID}\n".encode("ascii"))
        self.wait_until(lambda: session["state"] == "cancelled")
        self.grant(session)
        self.assertEqual(self.db.unlock_writes, 0)
        self.assertNotIn(f"AUTHORIZED {REQUEST_ID}\n".encode("ascii"), self.serial.writes)

    def test_controller_expiry_counts_wrong_face_once_for_lockout(self):
        self.db.settings.update(failLockout=True, lockoutAfter=2)
        session = self.start_scan()
        session["face_mismatch_observed"] = True
        self.runtime.cancel_esp32_scan(REQUEST_ID, self.serial)
        self.runtime.cancel_esp32_scan(REQUEST_ID, self.serial)
        self.assertEqual(self.runtime._hardware_denials, 1)
        self.runtime._release_session(session["id"])
        second = self.start_scan(request_id=OTHER_ID)
        second["face_mismatch_observed"] = True
        self.runtime.cancel_esp32_scan(OTHER_ID, self.serial)
        self.assertGreater(self.runtime._hardware_cooldown_until, time.monotonic())

    def test_cancellation_during_ack_wait_does_not_deadlock_or_grant(self):
        self.serial.reply = "AUTH_CANCEL"
        session = self.start_scan()
        worker, errors = self.grant_in_background(session)
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertNotEqual(session["state"], "granted")
        self.assertEqual(self.db.unlock_writes, 0)

    def test_cancel_before_ack_wait_prevents_authorized_write(self):
        session = self.start_scan()
        worker_ready = threading.Event()
        release_worker = threading.Event()
        original_authorize = self.link.authorize

        def delayed_authorize(request):
            worker_ready.set()
            self.assertTrue(release_worker.wait(2))
            return original_authorize(request)

        with patch.object(self.link, "authorize", side_effect=delayed_authorize):
            worker, errors = self.grant_in_background(session)
            self.assertTrue(worker_ready.wait(2))
            self.serial.incoming.put(f"AUTH_CANCEL {REQUEST_ID}\n".encode("ascii"))
            self.assertTrue(session["cancel_event"].wait(2))
            release_worker.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.db.unlock_writes, 0)
        self.assertNotIn(f"AUTHORIZED {REQUEST_ID}\n".encode("ascii"), self.serial.writes)

    def test_cancel_for_another_request_does_not_cancel_active_scan(self):
        session = self.start_scan()
        self.runtime.cancel_esp32_scan(OTHER_ID, self.serial)
        self.assertFalse(session["cancel_event"].is_set())
        self.grant(session)
        self.assertEqual(session["state"], "granted")

    def test_old_connection_cannot_deliver_even_a_matching_reply(self):
        old_request = AuthRequest("unlock", REQUEST_ID, SerialProbe())
        self.assertFalse(self.link.reply(old_request, "AUTHORIZED"))
        self.assertFalse(self.link.authorize(old_request))
        self.assertFalse(self.runtime.send_esp32_reply("AUTHORIZED", old_request))
        self.assertEqual(self.serial.writes, [])
        self.assertTrue(self.serial.is_open)

    def test_disconnect_wakes_pending_grant_without_persisting_unlock(self):
        self.serial.reply = None
        session = self.start_scan()
        worker, errors = self.grant_in_background(session)
        self.assertTrue(self.serial.authorization_sent.wait(2))
        self.serial.close()
        self.serial.incoming.put(OSError("USB removed"))
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.db.unlock_writes, 0)
        self.assertNotEqual(session["state"], "granted")

    def test_reconnect_clears_ignition_and_requires_fresh_unlock(self):
        session = self.start_scan()
        self.grant(session)
        self.runtime._release_session(session["id"])
        self.assertTrue(self.runtime.set_ignition(True)["ok"])
        old_connection = self.serial
        self.runtime.synchronized.clear()
        self.serial = SerialProbe()
        self.runtime.connection = self.serial
        old_connection.close()
        old_connection.incoming.put(OSError("USB removed"))
        self.assertTrue(self.runtime.synchronized.wait(2))
        snapshot = self.runtime._actuation.snapshot()
        self.assertFalse(snapshot.ignition_on)
        self.assertIsNone(snapshot.unlock_owner)
        self.assertEqual(self.db.lock_state, "locked")
        self.assertEqual(self.serial.writes[:2], [b"STOP\n", b"LOCK\n"])
        self.assertFalse(self.runtime.set_ignition(True)["ok"])


if __name__ == "__main__":
    unittest.main()
