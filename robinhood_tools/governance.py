from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

from .errors import PolicyViolation


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PolicyViolation(f"{name} must include a timezone.")


def _require_sha256(value: str, name: str) -> None:
    if not _SHA256.fullmatch(value):
        raise PolicyViolation(f"{name} must be a lowercase SHA-256 digest.")


@dataclass(frozen=True)
class EvidenceTimestamp:
    """A named source observation used in an investment decision."""

    source: str
    observed_at: datetime

    def validate(self, *, decision_at: datetime) -> None:
        if not self.source.strip():
            raise PolicyViolation("Evidence source names cannot be blank.")
        _require_aware(self.observed_at, f"{self.source} observed_at")
        if self.observed_at > decision_at:
            raise PolicyViolation(f"{self.source} evidence is dated after the decision.")


@dataclass(frozen=True)
class DecisionRecord:
    """Immutable provenance for a proposal; never execution authorization."""

    created_at: datetime
    model_name: str
    model_version: str
    prompt_sha256: str
    policy_version: str
    policy_sha256: str
    snapshot_sha256: str
    recommendation: Literal["hold", "add", "trim", "sell", "no_action"]
    score: int
    rationale: str
    evidence: tuple[EvidenceTimestamp, ...]
    schema_version: int = 1

    def validate(self) -> None:
        _require_aware(self.created_at, "created_at")
        for name, value in (
            ("model_name", self.model_name),
            ("model_version", self.model_version),
            ("policy_version", self.policy_version),
            ("rationale", self.rationale),
        ):
            if not value.strip():
                raise PolicyViolation(f"{name} cannot be blank.")
        for name, value in (
            ("prompt_sha256", self.prompt_sha256),
            ("policy_sha256", self.policy_sha256),
            ("snapshot_sha256", self.snapshot_sha256),
        ):
            _require_sha256(value, name)
        if not 0 <= self.score <= 100:
            raise PolicyViolation("Decision score must be between 0 and 100.")
        if self.schema_version < 1:
            raise PolicyViolation("Decision schema version must be positive.")
        sources = [item.source for item in self.evidence]
        if not sources or len(sources) != len(set(sources)):
            raise PolicyViolation("Decision evidence must be non-empty with unique source names.")
        for item in self.evidence:
            item.validate(decision_at=self.created_at)

    def canonical_payload(self) -> dict:
        self.validate()
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["evidence"] = [
            {"source": item.source, "observed_at": item.observed_at.isoformat()}
            for item in sorted(self.evidence, key=lambda item: item.source)
        ]
        return payload

    @property
    def decision_id(self) -> str:
        encoded = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
