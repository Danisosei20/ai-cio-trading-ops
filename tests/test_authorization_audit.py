"""Append-only, tamper-evident authorization audit events."""
import json
import tempfile
import unittest
from pathlib import Path

from robinhood_tools import authorization_audit as audit


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.path = Path(self.t.name) / "audit.jsonl"

    def tearDown(self):
        self.t.cleanup()

    def test_event_has_chain_and_masked_account(self):
        audit.emit("authorization_created", path=self.path, authorization_id="auth-1",
                   account_id="PA-123456789", outcome="ok", reason="issued")
        row = json.loads(self.path.read_text(encoding="utf-8").splitlines()[0])
        for field in ("event_id", "timestamp", "event", "prev_hash", "hash"):
            self.assertIn(field, row)
        self.assertNotIn("PA-123456789", json.dumps(row))
        self.assertIn("PA***789", json.dumps(row))

    def test_chain_verifies(self):
        for i in range(5):
            audit.emit("authorization_validated", path=self.path,
                       authorization_id=f"auth-{i}", outcome="ok")
        self.assertTrue(audit.verify_chain(self.path))

    def test_tampered_event_detected(self):
        audit.emit("authorization_created", path=self.path, authorization_id="auth-1")
        audit.emit("authorization_consumed", path=self.path, authorization_id="auth-1")
        lines = self.path.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[0])
        row["outcome"] = "ok"
        lines[0] = json.dumps(row, sort_keys=True)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertFalse(audit.verify_chain(self.path))

    def test_secret_material_never_logged(self):
        audit.emit("signature_failure", path=self.path,
                   authorization_id="auth-1", key="super-secret-key",
                   signature="deadbeef", reason="bad")
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("super-secret-key", text)
        self.assertNotIn("deadbeef", text)


if __name__ == "__main__":
    unittest.main()
