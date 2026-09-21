"""TLS replacement cannot preserve a previously issued device credential."""

from pathlib import Path
import tempfile
import unittest

import db
from security_fixtures import temporary_security
from wireless.host_identity import bind_host_identity
from wireless.security import SecurityError

DEVICE_ID = "acdd4d17-3796-476e-b2d6-91e6b70d957c"


class HostIdentityTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.fixture = temporary_security(self, root)

    def test_replacement_requires_explicit_offline_revocation(self):
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            bind_host_identity(db.get_conn, "1" * 64, DEVICE_ID)
        bind_host_identity(db.get_conn, "1" * 64, DEVICE_ID, rotate=True)
        with self.assertRaises(SecurityError):
            self.fixture.security.principal(self.fixture.token)
        bind_host_identity(db.get_conn, "1" * 64, DEVICE_ID)
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            bind_host_identity(db.get_conn, "2" * 64, DEVICE_ID)
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            bind_host_identity(db.get_conn, "1" * 64, "bcdd4d17-3796-476e-b2d6-91e6b70d957c")

    def test_rotation_audit_failure_rolls_back_revocation(self):
        with db.get_conn() as connection:
            connection.execute("CREATE TRIGGER reject_rotation BEFORE INSERT ON auth_logs "
                               "WHEN NEW.stage='tls_identity_rotation' "
                               "BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
        with self.assertRaises(Exception):
            bind_host_identity(db.get_conn, "1" * 64, DEVICE_ID, rotate=True)
        self.assertTrue(self.fixture.security.still_authorized(self.fixture.principal()))


if __name__ == "__main__":
    unittest.main()
