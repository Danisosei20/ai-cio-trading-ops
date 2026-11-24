"""Versioned HMAC key store: random keys, rotation, revocation, no leakage."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from robinhood_tools import authorization_keys as keys

ENV = dict(os.environ, CIO_AUTH_ALLOW_EPHEMERAL_IN_ENFORCED='1', CIO_AUTH_ALLOW_EPHEMERAL_KEY="1",
           EXECUTION_AUTH_HMAC_KEY="ephemeral-test-key",
           CIO_AUTH_ALLOW_PLAINTEXT_KEYSTORE="1")


class KeyStoreTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.path = Path(self.t.name) / "keys.json"
        self.store = keys.KeyStore(self.path, allow_plaintext=True)
        self.patcher = mock.patch.dict(os.environ, ENV, clear=True)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.t.cleanup()

    def test_initialize_creates_256_bit_random_key(self):
        kid = self.store.initialize()
        record = self.store.get(kid)
        raw = self.store.raw_key(kid)
        self.assertEqual(len(raw), 32)
        self.assertEqual(record["status"], "ACTIVE")
        self.assertEqual(record["algorithm"], "HMAC-SHA256")
        self.assertTrue(kid.startswith("kid-"))

    def test_initialize_is_idempotent(self):
        a = self.store.initialize()
        b = self.store.initialize()
        self.assertEqual(a, b)

    def test_no_key_material_in_store_file(self):
        kid = self.store.initialize()
        raw = self.store.raw_key(kid)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn(raw.hex(), text)
        self.assertNotIn(raw.decode("latin-1"), text)

    def test_signing_key_is_active(self):
        kid = self.store.initialize()
        with mock.patch.dict(os.environ, {}, clear=True):
            found_kid, _ = self.store.signing_key()
        self.assertEqual(found_kid, kid)

    def test_rotate_moves_old_key_to_verify_only(self):
        old = self.store.initialize()
        new = self.store.rotate()
        self.assertNotEqual(old, new)
        self.assertEqual(self.store.get(old)["status"], "VERIFY_ONLY")
        self.assertEqual(self.store.get(new)["status"], "ACTIVE")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.store.signing_key()[0], new)

    def test_verify_only_key_still_verifies_within_window(self):
        old = self.store.initialize()
        self.store.rotate(verify_hours=24)
        self.assertIsNotNone(self.store.verification_key(old))

    def test_revoked_key_cannot_verify_or_sign(self):
        kid = self.store.initialize()
        self.store.revoke(kid)
        self.assertIsNone(self.store.verification_key(kid))
        with self.assertRaises(keys.SecurityError):
            self.store.raw_key(kid)

    def test_compromised_key_is_not_reactivated(self):
        kid = self.store.initialize()
        self.store.revoke(kid, compromised=True)
        self.assertEqual(self.store.get(kid)["status"], "COMPROMISED")
        with self.assertRaises(keys.SecurityError):
            self.store.rotate_to(kid)

    def test_ephemeral_key_requires_explicit_flag(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(keys.ephemeral_key())
        with mock.patch.dict(os.environ,
                              {"EXECUTION_AUTH_HMAC_KEY": "x" + "0" * 20,
                               "CIO_AUTH_ALLOW_EPHEMERAL_KEY": "1",
                               "CIO_AUTH_ALLOW_EPHEMERAL_IN_ENFORCED": "1"}, clear=True):
            self.assertEqual(keys.ephemeral_key()[0], "ephemeral-test")

    def test_store_file_tamper_detected(self):
        self.store.initialize()
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        doc["keys"][0]["status"] = "RETIRED"
        self.path.write_text(json.dumps(doc), encoding="utf-8")
        self.assertFalse(self.store.verify_store())

    def test_status_masks_key_material(self):
        kid = self.store.initialize()
        status = self.store.status()
        self.assertEqual(status[0]["kid"], kid)
        for field in ("material", "secret", "raw"):
            self.assertNotIn(field, json.dumps(status).lower())

    def test_unknown_key_id_rejected(self):
        self.store.initialize()
        self.assertIsNone(self.store.verification_key("kid-unknown"))


if __name__ == "__main__":
    unittest.main()
