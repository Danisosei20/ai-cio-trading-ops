from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from .errors import PolicyViolation


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")
REQUIRED_METRICS = frozenset({"excess_return", "max_drawdown", "turnover", "slippage"})


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PolicyViolation(f"{name} must include a timezone.")


def _require_sha256(value: str, name: str) -> None:
    if not _SHA256.fullmatch(value):
        raise PolicyViolation(f"{name} must be a lowercase SHA-256 digest.")


@dataclass(frozen=True)
class ResearchMetric:
    name: str
    value: Decimal

    def validate(self) -> None:
        if not self.name.strip() or not self.value.is_finite():
            raise PolicyViolation("Research metrics require a name and finite decimal value.")


@dataclass(frozen=True)
class ResearchExperiment:
    """Immutable strategy hypothesis; acceptance never authorizes live trading."""

    created_at: datetime
    name: str
    hypothesis: str
    strategy_version: str
    baseline_version: str
    code_commit: str
    policy_sha256: str
    parameter_sha256: str
    replay_snapshot_sha256: str
    expected_benefit: str
    rollback_criteria: str
    minimum_observations: int = 10
    schema_version: int = 1

    def validate(self) -> None:
        _require_aware(self.created_at, "created_at")
        for name, value in (
            ("name", self.name),
            ("hypothesis", self.hypothesis),
            ("strategy_version", self.strategy_version),
            ("baseline_version", self.baseline_version),
            ("expected_benefit", self.expected_benefit),
            ("rollback_criteria", self.rollback_criteria),
        ):
            if not value.strip():
                raise PolicyViolation(f"{name} cannot be blank.")
        if self.strategy_version == self.baseline_version:
            raise PolicyViolation("Experiment strategy and baseline versions must differ.")
        if not _COMMIT.fullmatch(self.code_commit):
            raise PolicyViolation("code_commit must be a 7- to 64-character lowercase Git commit hash.")
        for name, value in (
            ("policy_sha256", self.policy_sha256),
            ("parameter_sha256", self.parameter_sha256),
            ("replay_snapshot_sha256", self.replay_snapshot_sha256),
        ):
            _require_sha256(value, name)
        if self.minimum_observations < 10:
            raise PolicyViolation("Research experiments require at least 10 comparable observations.")
        if self.schema_version < 1:
            raise PolicyViolation("Research experiment schema version must be positive.")

    def canonical_payload(self) -> dict:
        self.validate()
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        return payload

    @property
    def experiment_id(self) -> str:
        encoded = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ResearchRun:
    experiment_id: str
    observed_at: datetime
    market_regime: str
    observation_count: int
    dataset_sha256: str
    artifact_sha256: str
    metrics: tuple[ResearchMetric, ...]

    def validate(self) -> None:
        _require_sha256(self.experiment_id, "experiment_id")
        _require_aware(self.observed_at, "observed_at")
        if self.market_regime not in {"risk_on", "neutral", "risk_off"}:
            raise PolicyViolation("Research run market regime must be risk_on, neutral, or risk_off.")
        if self.observation_count < 1:
            raise PolicyViolation("Research runs must contain at least one observation.")
        _require_sha256(self.dataset_sha256, "dataset_sha256")
        _require_sha256(self.artifact_sha256, "artifact_sha256")
        names = [metric.name for metric in self.metrics]
        if len(names) != len(set(names)):
            raise PolicyViolation("Research run metric names must be unique.")
        missing = sorted(REQUIRED_METRICS - set(names))
        if missing:
            raise PolicyViolation(f"Research run is missing required metrics: {', '.join(missing)}")
        for metric in self.metrics:
            metric.validate()

    def canonical_payload(self) -> dict:
        self.validate()
        return {
            "experiment_id": self.experiment_id,
            "observed_at": self.observed_at.isoformat(),
            "market_regime": self.market_regime,
            "observation_count": self.observation_count,
            "dataset_sha256": self.dataset_sha256,
            "artifact_sha256": self.artifact_sha256,
            "metrics": [
                {"name": metric.name, "value": str(metric.value)}
                for metric in sorted(self.metrics, key=lambda metric: metric.name)
            ],
        }

    @property
    def run_id(self) -> str:
        encoded = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
