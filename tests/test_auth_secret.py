"""Temporary PIN-secret persistence and loss handling; no existing identity touched."""

from pathlib import Path
import tempfile
import unittest

from wireless.auth_secret import load_auth_secret


class AuthSecretTests(unittest.TestCase):
    def test_secret_is_private_persistent_and_not_replaced(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config = folder / "device.json"
        first = load_auth_secret(config, credentials_exist=False)
        self.assertEqual(len(first), 32)
        self.assertEqual(load_auth_secret(config, credentials_exist=True), first)
        self.assertFalse(config.exists())
        config.with_suffix(".auth.key").write_bytes(b"broken")
        with self.assertRaisesRegex(RuntimeError, "invalid"):
            load_auth_secret(config, credentials_exist=True)
        self.assertEqual(config.with_suffix(".auth.key").read_bytes(), b"broken")

    def test_missing_secret_with_accounts_fails_without_regeneration(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config = folder / "device.json"
        with self.assertRaisesRegex(RuntimeError, "not regenerated"):
            load_auth_secret(config, credentials_exist=True)
        self.assertFalse(config.with_suffix(".auth.key").exists())


if __name__ == "__main__":
    unittest.main()
