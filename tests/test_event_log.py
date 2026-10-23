import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import db
import db_api
from event_log import format_event


class EventLogProjectionTests(unittest.TestCase):
    def test_known_events_have_safe_human_readable_outcomes(self):
        cases = (
            (
                "success",
                format_event(
                    "face_scan",
                    "ok",
                    "Match confidence=0.98; camera=front",
                    subject_name="Ada",
                ),
                "Face access granted",
                "Ada was recognized, and the unlock command was sent.",
                "success",
            ),
            (
                "disabled",
                format_event(
                    "face_scan",
                    "fail",
                    "Access denied: face access is disabled.",
                    subject_name="Ada",
                ),
                "Face access denied",
                "Ada was recognized, but face access is disabled.",
                "denied",
            ),
            (
                "revoked",
                format_event(
                    "face_scan",
                    "fail",
                    "Access was revoked before the scan completed.",
                    subject_name="Ada",
                ),
                "Face access denied",
                "Face access changed before the scan completed for Ada.",
                "denied",
            ),
            (
                "cancelled",
                format_event(
                    "enrollment",
                    "fail",
                    "Enroll cancelled.",
                    subject_name="Ada",
                ),
                "Face enrollment cancelled",
                "Face enrollment was cancelled for Ada.",
                "cancelled",
            ),
            (
                "error",
                format_event(
                    "face_scan",
                    "error",
                    "Camera exception: device path /dev/video0",
                    subject_name="Ada",
                ),
                "Face scan failed",
                "Face scan could not be completed for Ada.",
                "error",
            ),
        )

        for label, event, title, summary, outcome in cases:
            with self.subTest(label=label):
                self.assertEqual(event["title"], title)
                self.assertEqual(event["summary"], summary)
                self.assertEqual(event["outcome"], outcome)

        self.assertNotIn("recognized", cases[2][1]["summary"].lower())

    def test_malformed_scan_timing_state_is_safe(self):
        malformed_details = (
            json.dumps(["granted", "secret-token"]),
            json.dumps({"state": ["granted", "secret-token"]}),
            json.dumps({"state": {"value": "granted"}}),
        )

        for detail in malformed_details:
            with self.subTest(detail=detail):
                event = format_event("scan_timing", "", detail)
                self.assertEqual(event["title"], "Face scan timing")
                self.assertEqual(event["summary"], "Unlock face scan finished.")
                self.assertEqual(event["outcome"], "info")
                self.assertNotIn("secret-token", event["summary"])

    def test_raw_unknown_detail_does_not_reach_display_text(self):
        secret = "Bearer raw-secret-token camera=/dev/video0"
        event = format_event("legacy_custom_stage", "ok", secret)

        self.assertEqual(
            event,
            {
                "title": "System event",
                "summary": "The event completed successfully.",
                "outcome": "success",
            },
        )
        self.assertNotIn(secret, event["title"])
        self.assertNotIn(secret, event["summary"])

    def test_unknown_role_detail_does_not_claim_a_role(self):
        event = format_event(
            "security_role_changed",
            "ok",
            "admin=unexpected-secret",
            subject_name="Ada",
        )

        self.assertEqual(event["summary"], "Role saved for Ada.")
        self.assertNotIn("User", event["summary"])
        self.assertNotIn("Administrator", event["summary"])


class EventLogScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path_patch = mock.patch.object(
            db,
            "DB_PATH",
            str(Path(self.temp_dir.name) / "faceid.db"),
        )
        self.db_path_patch.start()
        db.init_db()
        self._seed_data()

    def tearDown(self):
        self.db_path_patch.stop()
        self.temp_dir.cleanup()

    def _seed_data(self):
        users = (
            ("owner", "Owner"),
            ("admin", "Admin"),
            ("ada", "Ada"),
            ("bob", "Bob"),
        )
        logs = (
            ("owner-actor", None, "owner", "user"),
            ("owner-subject", "owner", "admin", "user"),
            ("admin-event", None, "admin", "user"),
            ("ada-actor", None, "ada", "user"),
            ("ada-subject", "ada", "admin", "user"),
            ("bob-event", "bob", "bob", "user"),
            ("system-event", None, None, "system"),
            ("legacy-event", "ada", None, "legacy"),
        )
        with db.get_conn() as conn:
            conn.execute(
                "CREATE TABLE commissioning_state ("
                "id INTEGER PRIMARY KEY, ownership TEXT, owner_user_id TEXT)"
            )
            conn.execute(
                "INSERT INTO commissioning_state VALUES (1, 'claimed', 'owner')"
            )
            conn.executemany(
                "INSERT INTO users "
                "(id, name, active, face_access, created_at) "
                "VALUES (?, ?, 1, 1, 1)",
                users,
            )
            conn.executemany(
                "INSERT INTO auth_logs "
                "(id, user_id, actor_user_id, actor_kind, stage, result, "
                "detail, ts) VALUES (?, ?, ?, ?, 'settings', 'ok', ?, ?)",
                (
                    (*row, f"private detail for {row[0]}", index)
                    for index, row in enumerate(logs, start=1)
                ),
            )

    def _principal(self, user_id, *, owner=False, admin=False):
        return SimpleNamespace(
            user_id=user_id,
            is_owner=owner,
            is_admin=admin,
        )

    def test_owner_sees_all_events_with_legacy_detail_preserved(self):
        logs = db_api.list_logs_for_ui(
            principal=self._principal("owner", owner=True),
        )

        self.assertEqual(
            {row["id"] for row in logs},
            {
                "owner-actor",
                "owner-subject",
                "admin-event",
                "ada-actor",
                "ada-subject",
                "bob-event",
                "system-event",
                "legacy-event",
            },
        )
        legacy = next(row for row in logs if row["id"] == "legacy-event")
        self.assertEqual(legacy["detail"], "private detail for legacy-event")
        self.assertEqual(
            set(legacy),
            {
                "id",
                "ts",
                "type",
                "ok",
                "detail",
                "title",
                "summary",
                "outcome",
            },
        )

    def test_admin_excludes_owner_and_legacy_events(self):
        logs = db_api.list_logs_for_ui(
            principal=self._principal("admin", admin=True),
        )

        self.assertEqual(
            {row["id"] for row in logs},
            {
                "admin-event",
                "ada-actor",
                "ada-subject",
                "bob-event",
                "system-event",
            },
        )
        for row in logs:
            self.assertEqual(row["detail"], "settings: ok")

    def test_ordinary_user_sees_only_their_nonlegacy_events(self):
        logs = db_api.list_logs_for_ui(principal=self._principal("ada"))

        self.assertEqual(
            {row["id"] for row in logs},
            {"ada-actor", "ada-subject"},
        )
        subject_event = next(
            row for row in logs if row["id"] == "ada-subject"
        )
        self.assertEqual(subject_event["summary"], "Admin: settings updated.")
        self.assertEqual(subject_event["detail"], "settings: ok")


if __name__ == "__main__":
    unittest.main()
