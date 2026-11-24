#!/usr/bin/env python3
"""Versioned, protected HMAC key management for execution authorizations.

Keys are 256-bit CSPRNG values. On Windows the private material is protected
with user-scoped DPAPI; elsewhere an explicit plaintext test mode is required.
No raw key material is written to authorization records, logs or API responses.

Security limitation (see docs/security/authorization-key-threat-model.md): all
desk processes run as one Windows user, so DPAPI+ACL protects at rest but does
not separate signing authority from execution workers.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ALGORITHM = "HMAC-SHA256"
KEY_BYTES = 32
STORE_VERSION = 1
ENV_KEY = "EXECUTION_AUTH_HMAC_KEY"
ENV_ALLOW_EPHEMERAL = "CIO_AUTH_ALLOW_EPHEMERAL_KEY"
ENV_ALLOW_PLAINTEXT = "CIO_AUTH_ALLOW_PLAINTEXT_KEYSTORE"
ENV_STORE = "CIO_AUTH_KEYSTORE"
EPHEMERAL_KID = "ephemeral-test"
ACTIVE = {"ACTIVE"}
VERIFYING = {"ACTIVE", "VERIFY_ONLY"}
TERMINAL = {"RETIRED", "REVOKED", "COMPROMISED"}


class SecurityError(RuntimeError):
    """Raised when key material is unavailable, revoked or tampered with."""


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: datetime | None = None) -> str:
    return (dt or _utc()).isoformat()


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def default_store_path() -> Path:
    configured = os.environ.get(ENV_STORE, "").strip()
    if configured:
        return Path(configured)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return base / "ai-cio-trading-ops" / "authorization_keys.json"
    return Path.home() / ".cio_auth" / "authorization_keys.json"


def ephemeral_key() -> tuple[str, bytes] | None:
    """Explicit, test-only key source. Never used operationally by default."""
    raw = os.environ.get(ENV_KEY, "").strip()
    if not raw or os.environ.get(ENV_ALLOW_EPHEMERAL) != "1":
        return None
    if (os.environ.get("EXECUTION_AUTHORIZATION_ENABLED") == "true"
            and os.environ.get("CIO_AUTH_ALLOW_EPHEMERAL_IN_ENFORCED") != "1"):
        return None
    value = raw.encode("utf-8")
    if len(value) < 16:
        return None
    return EPHEMERAL_KID, value


def _dpapi_protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise SecurityError("DPAPI available only on Windows")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    source = (ctypes.c_byte * len(data)).from_buffer_copy(data)
    blob_in = DATA_BLOB(len(data), source)
    blob_out = DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))
    if not ok:
        raise SecurityError("DPAPI protect failed")
    try:
        out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)
    return out


def _dpapi_unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise SecurityError("DPAPI available only on Windows")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    source = (ctypes.c_byte * len(data)).from_buffer_copy(data)
    blob_in = DATA_BLOB(len(data), source)
    blob_out = DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))
    if not ok:
        raise SecurityError("DPAPI unprotect failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


class KeyStore:
    """Durable versioned keystore with atomic persistence."""

    def __init__(self, path: Path | None = None, *, allow_plaintext: bool = False):
        self.path = Path(path) if path is not None else default_store_path()
        self.allow_plaintext = allow_plaintext or os.environ.get(
            ENV_ALLOW_PLAINTEXT) == "1"

    # ----- persistence -------------------------------------------------
    def _load(self) -> dict:
        self._check_path()
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": STORE_VERSION, "keys": [], "integrity": ""}
        except (OSError, ValueError) as exc:
            raise SecurityError(f"keystore unreadable: {type(exc).__name__}") from exc
        if not isinstance(doc, dict) or doc.get("version") != STORE_VERSION:
            raise SecurityError("keystore version unsupported")
        keys = doc.get("keys")
        if not isinstance(keys, list):
            raise SecurityError("keystore malformed")
        return doc

    def _save(self, doc: dict) -> None:
        keys = doc.get("keys", [])
        doc["integrity"] = self._integrity(keys)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = json.dumps(doc, sort_keys=True, indent=2)
        if sys.platform == "win32":
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            fd = os.open(str(tmp), flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
        else:
            tmp.write_text(payload, encoding="utf-8")
            try:
                tmp.chmod(0o600)
            except OSError:
                pass
        os.replace(tmp, self.path)
        if sys.platform == "win32":
            self._restrict_acl()

    def _restrict_acl(self) -> None:
        """Best-effort: owner + SYSTEM only. Owner must verify on target host."""
        import subprocess
        try:
            subprocess.run(["icacls", str(self.path), "/inheritance:r",
                            "/grant:r", f"{os.environ.get('USERNAME', '*SID')}:(F)",
                            "SYSTEM:(F)"],
                           capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            pass

    def _integrity(self, keys: list) -> str:
        mac = b"auth-keys-v2"
        if keys and isinstance(keys[0], dict) and keys[0].get("material"):
            try:
                mac = self._unprotect(keys[0])
            except Exception:  # noqa: BLE001
                pass
        return hmac.new(mac, _canonical(keys), hashlib.sha256).hexdigest()

    def _check_path(self) -> None:
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise SecurityError("keystore path must not be a symlink or junction")

    def verify_store(self) -> bool:
        try:
            doc = self._load()
        except SecurityError:
            return False
        return doc.get("integrity") == self._integrity(doc.get("keys", []))

    # ----- key lifecycle ----------------------------------------------
    def _protect(self, raw: bytes) -> str:
        if sys.platform == "win32" and not self.allow_plaintext:
            protected = _dpapi_protect(raw)
        else:
            protected = raw
        return base64.b64encode(protected).decode("ascii")

    def _unprotect(self, record: dict) -> bytes:
        raw = base64.b64decode(record["material"])
        if record.get("protection") == "dpapi":
            value = _dpapi_unprotect(raw)
        elif record.get("protection") == "plaintext-test" and self.allow_plaintext:
            value = raw
        else:
            raise SecurityError("key protection unsupported")
        if len(value) != KEY_BYTES:
            raise SecurityError("key material length invalid")
        return value

    def initialize(self) -> str:
        doc = self._load()
        active = [k for k in doc["keys"] if k["status"] == "ACTIVE"]
        if active:
            return str(active[0]["kid"])
        raw = secrets.token_bytes(KEY_BYTES)
        kid = "kid-" + hashlib.sha256(raw).hexdigest()[:16]
        record = {
            "kid": kid, "algorithm": ALGORITHM,
            "created_at": _stamp(), "activated_at": _stamp(),
            "retired_at": None, "verify_until": None, "status": "ACTIVE",
            "source": "keystore",
            "protection": "dpapi" if sys.platform == "win32" and not self.allow_plaintext
                          else "plaintext-test",
            "material": self._protect(raw),
        }
        doc["keys"].append(record)
        self._save(doc)
        return kid

    def rotate(self, *, verify_hours: int = 24) -> str:
        doc = self._load()
        if not self.verify_store():
            raise SecurityError("keystore integrity failure")
        now = _utc()
        for record in doc["keys"]:
            if record["status"] == "ACTIVE":
                record["status"] = "VERIFY_ONLY"
                record["verify_until"] = _stamp(now + timedelta(hours=verify_hours))
        raw = secrets.token_bytes(KEY_BYTES)
        kid = "kid-" + hashlib.sha256(raw).hexdigest()[:16]
        doc["keys"].append({
            "kid": kid, "algorithm": ALGORITHM, "created_at": _stamp(now),
            "activated_at": _stamp(now), "retired_at": None, "verify_until": None,
            "status": "ACTIVE", "source": "keystore",
            "protection": "dpapi" if sys.platform == "win32" and not self.allow_plaintext
                          else "plaintext-test",
            "material": self._protect(raw),
        })
        self._save(doc)
        return kid

    def rotate_to(self, kid: str) -> None:
        raise SecurityError("manual key reactivation is not permitted")

    def revoke(self, kid: str, *, compromised: bool = False) -> None:
        doc = self._load()
        if not self.verify_store():
            raise SecurityError("keystore integrity failure")
        for record in doc["keys"]:
            if record["kid"] == kid:
                record["status"] = "COMPROMISED" if compromised else "REVOKED"
                record["verify_until"] = None
                record["material"] = ""
        self._save(doc)

    def retire(self, kid: str) -> None:
        doc = self._load()
        for record in doc["keys"]:
            if record["kid"] == kid and record["status"] == "VERIFY_ONLY":
                record["status"] = "RETIRED"
                record["material"] = ""
        self._save(doc)

    def get(self, kid: str) -> dict | None:
        if not self.verify_store():
            raise SecurityError("keystore integrity failure")
        for record in self._load()["keys"]:
            if record["kid"] == kid:
                return dict(record)
        return None

    def raw_key(self, kid: str) -> bytes:
        record = self.get(kid)
        if record is None or record["status"] != "ACTIVE":
            raise SecurityError("key unavailable or not active")
        return self._unprotect(record)

    def verification_key(self, kid: str) -> bytes | None:
        if kid == EPHEMERAL_KID:
            ephemeral = ephemeral_key()
            return ephemeral[1] if ephemeral else None
        try:
            record = self.get(kid)
        except SecurityError:
            return None
        if record is None or record["status"] not in VERIFYING:
            return None
        if record["status"] == "VERIFY_ONLY" and record.get("verify_until"):
            try:
                if _utc() > datetime.fromisoformat(record["verify_until"]):
                    return None
            except (TypeError, ValueError):
                return None
        try:
            return self._unprotect(record)
        except SecurityError:
            return None

    def signing_key(self) -> tuple[str, bytes] | None:
        ephemeral = ephemeral_key()
        if ephemeral:
            return ephemeral
        if not self.verify_store():
            raise SecurityError("keystore integrity failure")
        for record in self._load()["keys"]:
            if record["status"] == "ACTIVE":
                return str(record["kid"]), self.raw_key(str(record["kid"]))
        return None

    def status(self) -> list[dict[str, Any]]:
        try:
            doc = self._load()
        except SecurityError:
            return []
        return [{"kid": k["kid"], "algorithm": k["algorithm"],
                 "status": k["status"], "created_at": k["created_at"],
                 "activated_at": k["activated_at"], "retired_at": k["retired_at"],
                 "verify_until": k["verify_until"], "source": k["source"]}
                for k in doc["keys"]]


_STORE: KeyStore | None = None


def get_store() -> KeyStore:
    global _STORE
    if _STORE is None:
        _STORE = KeyStore()
    return _STORE


def set_store(store: KeyStore | None) -> None:
    global _STORE
    _STORE = store
