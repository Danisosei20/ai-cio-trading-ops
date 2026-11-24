"""Security telemetry is read-only and never exposes key material."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import agent_office
from robinhood_tools import authorization_keys as keys

ENV = dict(os.environ, CIO_AUTH_ALLOW_EPHEMERAL_IN_ENFORCED='1', CIO_AUTH_ALLOW_EPHEMERAL_KEY="1",
           EXECUTION_AUTH_HMAC_KEY="dashboard-secret-key",
           CIO_AUTH_ALLOW_PLAINTEXT_KEYSTORE="1")


class SecurityDashboardTests(unittest.TestCase):
    def test_payload_includes_security_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "outputs/paper").mkdir(parents=True)
            p = agent_office.execution_safety_payload(root)
        for field in ("authorization_service_health", "signing_key",
                      "verification_key_count", "last_rotation",
                      "revoked_authorization_count", "failed_validations"):
            self.assertIn(field, p)
        self.assertTrue(p["read_only"])
        self.assertIn("phase7_3", p)
        self.assertIn("audit_chain_health", p["phase7_3"])
        self.assertIn("MOCKED", p["phase7_3"]["rehearsal_label"])

    def test_payload_has_no_secret(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "outputs/paper").mkdir(parents=True)
            keystore = root / "keys.json"
            with mock.patch.dict(os.environ, dict(ENV, CIO_AUTH_KEYSTORE=str(keystore)), clear=True):
                keys.KeyStore(keystore, allow_plaintext=True).initialize()
                payload = agent_office.execution_safety_payload(root)
        text = json.dumps(payload)
        self.assertNotIn("dashboard-secret-key", text)
        self.assertNotIn("material", text)


if __name__ == "__main__":
    unittest.main()
