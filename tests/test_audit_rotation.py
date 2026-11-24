"""Deterministic audit-chain verification across key lifecycle events."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from robinhood_tools import authorization_audit as audit
from robinhood_tools import authorization_keys as keys

ENV = dict(os.environ, CIO_AUTH_ALLOW_PLAINTEXT_KEYSTORE="1",
           CIO_AUTH_ALLOW_EPHEMERAL_KEY="1",
           CIO_AUTH_ALLOW_EPHEMERAL_IN_ENFORCED="1",
           EXECUTION_AUTH_HMAC_KEY="rotation-test-key-32-bytes-long")


class AuditRotationTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        self.store = keys.KeyStore(self.root / "keys.json", allow_plaintext=True)
        self.audit = self.root / "audit.jsonl"
        self.patcher = mock.patch.dict(os.environ, ENV, clear=True)
        self.patcher.start()
        self.store_patch = mock.patch.object(keys, "get_store", return_value=self.store)
        self.store_patch.start()

    def tearDown(self):
        self.store_patch.stop()
        self.patcher.stop()
        self.t.cleanup()

    def _emit(self, event):
        audit.emit(event, path=self.audit, authorization_id=event)

    def test_one_key_chain(self):
        self.store.initialize()
        self._emit("a")
        self._emit("b")
        self.assertTrue(audit.verify_chain(self.audit))

    def test_chain_across_rotation_and_retirement(self):
        k1 = self.store.initialize()
        self._emit("before")
        k2 = self.store.rotate()
        self._emit("after-rotate")
        self.store.retire(k1)
        self._emit("after-retire")
        self.assertEqual(self.store.get(k1)["status"], "RETIRED")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.store.signing_key()[0], k2)
        self.assertTrue(audit.verify_chain(self.audit))

    def test_three_rotations(self):
        self.store.initialize()
        self._emit("seed")
        for i in range(3):
            self.store.rotate()
            self._emit(f"rotation-{i}")
        self.assertTrue(audit.verify_chain(self.audit))

    def test_tampered_event_detected_after_rotation(self):
        self.store.initialize()
        self._emit("before")
        self.store.rotate()
        self._emit("after")
        lines = self.audit.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[0])
        row["outcome"] = "tampered"
        lines[0] = json.dumps(row, sort_keys=True)
        self.audit.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertFalse(audit.verify_chain(self.audit))

    def test_duplicate_event_id_detected(self):
        self.store.initialize()
        with mock.patch.object(audit.uuid, "uuid4", return_value=type("U", (), {"hex": "abc" * 10})()):
            self._emit("one")
            self._emit("two")
        self.assertFalse(audit.verify_chain(self.audit))

    def test_forged_row_with_unknown_kid_rejected(self):
        import hashlib
        import hmac
        self.store.initialize()
        self._emit("genuine")
        lines = self.audit.read_text(encoding="utf-8").splitlines()
        last = json.loads(lines[-1])
        body = {"event": "authorization_consumed", "event_id": "evt-forged",
                "kid": "unknown-kid", "prev_hash": last["hash"],
                "authorization_id": "forged", "outcome": "FORGED"}
        digest = hmac.new(b"audit-v3-unkeyed", json.dumps(
            {"prev": last["hash"], **body}, sort_keys=True,
            separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
        body["hash"] = digest
        with self.audit.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(body, sort_keys=True) + "\n")
        self.assertFalse(audit.verify_chain(self.audit))

    def test_revoked_key_cannot_sign_new_authorization(self):
        kid = self.store.initialize()
        self.store.revoke(kid)
        from robinhood_tools import execution_authorization as ea
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(Exception):
                ea._sign({"state": "PENDING"})


if __name__ == "__main__":
    unittest.main()
