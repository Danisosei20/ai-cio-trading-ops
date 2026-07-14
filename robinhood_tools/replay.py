from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime

from .errors import PolicyViolation


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PolicyViolation(f"{name} must include a timezone.")


@dataclass(frozen=True)
class PointInTimeObservation:
    name: str
    observed_at: datetime
    effective_at: datetime
    content_sha256: str


@dataclass(frozen=True)
class ReplaySnapshot:
    """Look-ahead-bias guard for a future historical replay engine."""

    decision_at: datetime
    observations: tuple[PointInTimeObservation, ...]
    required_names: tuple[str, ...]

    def validate(self) -> None:
        _require_aware(self.decision_at, "decision_at")
        names = [item.name for item in self.observations]
        if len(names) != len(set(names)):
            raise PolicyViolation("Replay observations must have unique names.")
        missing = sorted(set(self.required_names) - set(names))
        if missing:
            raise PolicyViolation(f"Replay snapshot is missing required observations: {', '.join(missing)}")
        for item in self.observations:
            if not item.name.strip():
                raise PolicyViolation("Replay observation names cannot be blank.")
            _require_aware(item.observed_at, f"{item.name} observed_at")
            _require_aware(item.effective_at, f"{item.name} effective_at")
            if item.observed_at > self.decision_at or item.effective_at > self.decision_at:
                raise PolicyViolation(f"{item.name} contains information unavailable at decision time.")
            if not _SHA256.fullmatch(item.content_sha256):
                raise PolicyViolation(f"{item.name} content hash must be a lowercase SHA-256 digest.")

    @property
    def digest(self) -> str:
        self.validate()
        payload = {
            "decision_at": self.decision_at.isoformat(),
            "required_names": sorted(self.required_names),
            "observations": [
                {
                    "name": item.name,
                    "observed_at": item.observed_at.isoformat(),
                    "effective_at": item.effective_at.isoformat(),
                    "content_sha256": item.content_sha256,
                }
                for item in sorted(self.observations, key=lambda item: item.name)
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
