"""Operational key CLI: no secret output, explicit operations only."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import auth_key_cli

ENV = dict(os.environ, CIO_AUTH_ALLOW_EPHEMERAL_IN_ENFORCED='1', CIO_AUTH_ALLOW_EPHEMERAL_KEY="1",
           EXECUTION_AUTH_HMAC_KEY="cli-test-key-long-test-key-32",
           CIO_AUTH_ALLOW_PLAINTEXT_KEYSTORE="1")


class KeyCliTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.path = Path(self.t.name) / "keys.json"
        self.env = dict(ENV, CIO_AUTH_KEYSTORE=str(self.path))
        self.patcher = mock.patch.dict(os.environ, self.env, clear=True)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.t.cleanup()

    def test_status_has_no_secret(self):
        from robinhood_tools.authorization_keys import KeyStore
        KeyStore(self.path, allow_plaintext=True).initialize()
        rc, out = auth_key_cli.main(["status"])
        self.assertEqual(rc, 0)
        self.assertNotIn("cli-test-key", out)
        self.assertIn("ACTIVE", out)

    def test_initialize_and_rotate(self):
        rc, out = auth_key_cli.main(["initialize"])
        self.assertEqual(rc, 0)
        self.assertIn("kid-", out)
        rc, out = auth_key_cli.main(["rotate"])
        self.assertEqual(rc, 0)
        self.assertIn("kid-", out)
        rc, out = auth_key_cli.main(["status"])
        self.assertIn("VERIFY_ONLY", out)
        self.assertIn("ACTIVE", out)

    def test_revoke_marks_compromised(self):
        from robinhood_tools.authorization_keys import KeyStore
        store = KeyStore(self.path, allow_plaintext=True)
        kid = store.initialize()
        rc, out = auth_key_cli.main(["revoke", kid, "--compromised"])
        self.assertEqual(rc, 0)
        self.assertEqual(store.get(kid)["status"], "COMPROMISED")

    def test_verify_store_ok(self):
        from robinhood_tools.authorization_keys import KeyStore
        KeyStore(self.path, allow_plaintext=True).initialize()
        rc, out = auth_key_cli.main(["verify-store"])
        self.assertEqual(rc, 0)
        self.assertIn("OK", out)

    def test_no_operation_without_explicit_command(self):
        rc, out = auth_key_cli.main([])
        self.assertNotEqual(rc, 0)
        self.assertIn("command required", out.lower())


if __name__ == "__main__":
    unittest.main()
