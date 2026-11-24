"""Explicit administrative bootstrap of authorization stores."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import auth_bootstrap

ENV = dict(os.environ, CIO_AUTH_ALLOW_PLAINTEXT_KEYSTORE="1")


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        self.patch = mock.patch.dict(os.environ, dict(
            ENV, CIO_AUTH_KEYSTORE=str(self.root / "secure" / "keys.json"),
            EXECUTION_AUTHORIZATION_ENABLED="false"), clear=True)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.t.cleanup()

    def test_initialize_creates_all_stores_and_redacted_report(self):
        rc, report = auth_bootstrap.main(["initialize", "--root", str(self.root),
                                          "--admin-confirm", "INITIALIZE-AUTH-STORE"])
        self.assertEqual(rc, 0)
        self.assertTrue((self.root / "outputs/paper/authorization_audit.jsonl").exists())
        self.assertTrue((self.root / "outputs/paper/execution_authorizations.sqlite").exists())
        self.assertIn("kid-", report)
        self.assertNotIn("material", report.lower())
        self.assertEqual(json.loads(report)["enforcement"], "off")

    def test_refuses_overwrite_without_force(self):
        auth_bootstrap.main(["initialize", "--root", str(self.root),
                             "--admin-confirm", "INITIALIZE-AUTH-STORE"])
        rc, report = auth_bootstrap.main(["initialize", "--root", str(self.root),
                                          "--admin-confirm", "INITIALIZE-AUTH-STORE"])
        self.assertNotEqual(rc, 0)
        self.assertIn("exists", report.lower())

    def test_requires_admin_confirmation(self):
        rc, report = auth_bootstrap.main(["initialize", "--root", str(self.root)])
        self.assertNotEqual(rc, 0)
        self.assertIn("confirm", report.lower())


if __name__ == "__main__":
    unittest.main()
