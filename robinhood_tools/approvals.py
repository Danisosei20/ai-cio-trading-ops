from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Literal

from .errors import PolicyViolation
from .models import EquityOrderRequest, OrderReview

ApprovalStatus = Literal[
    "pending", "approved", "executing", "executed", "failed",
    "rejected", "expired", "reconciliation_required",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def order_fingerprint(request: EquityOrderRequest) -> str:
    payload = {
        "account_id": request.account_id,
        "symbol": request.symbol.upper(),
        "side": request.side,
        "order_type": request.order_type,
        "time_in_force": request.time_in_force,
        "quantity": _decimal_text(request.quantity),
        "notional": _decimal_text(request.notional),
        "limit_price": _decimal_text(request.limit_price),
        "stop_price": _decimal_text(request.stop_price),
        "extended_hours": request.extended_hours,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class ApprovalRecord:
    approval_id: str
    review_id: str
    order_fingerprint: str
    account_id: str
    symbol: str
    created_at: str
    expires_at: str
    status: ApprovalStatus = "pending"
    approved_at: str | None = None
    executed_at: str | None = None
    broker_review: dict | None = None


class JsonApprovalStore:
    """Small durable approval ledger with atomic writes and strict state transitions."""

    def __init__(self, path: str | Path, now: Callable[[], datetime] = utc_now):
        self.path = Path(path)
        self.now = now

    def create(
        self,
        request: EquityOrderRequest,
        review: OrderReview,
        *,
        window_minutes: int,
        approval_id: str | None = None,
    ) -> ApprovalRecord:
        if window_minutes <= 0:
            raise PolicyViolation("approval_window_minutes must be greater than zero.")
        created = self.now()
        record = ApprovalRecord(
            approval_id=approval_id or str(uuid.uuid4()),
            review_id=review.review_id,
            order_fingerprint=order_fingerprint(request),
            account_id=request.account_id,
            symbol=request.symbol.upper(),
            created_at=created.isoformat(),
            expires_at=(created + timedelta(minutes=window_minutes)).isoformat(),
            broker_review=review.raw,
        )
        records = self._load()
        records[record.approval_id] = record
        self._save(records)
        return record

    def approve(self, approval_id: str) -> ApprovalRecord:
        record, records = self._get(approval_id)
        self._expire_if_needed(record, records)
        if record.status != "pending":
            raise PolicyViolation(f"Approval {approval_id!r} is {record.status!r}, not pending.")
        record.status = "approved"
        record.approved_at = self.now().isoformat()
        self._save(records)
        return record

    def require_for_placement(self, approval_id: str, request: EquityOrderRequest, review_id: str) -> ApprovalRecord:
        record, records = self._get(approval_id)
        self._expire_if_needed(record, records)
        if record.status != "approved":
            raise PolicyViolation(f"Approval {approval_id!r} is {record.status!r}, not approved.")
        if record.review_id != review_id:
            raise PolicyViolation("The approval ID does not match the broker review ID.")
        if record.order_fingerprint != order_fingerprint(request):
            raise PolicyViolation("Reviewed order parameters changed; a fresh broker review and approval are required.")
        return record

    def mark_executed(self, approval_id: str) -> ApprovalRecord:
        record, records = self._get(approval_id)
        if record.status != "approved":
            raise PolicyViolation(f"Approval {approval_id!r} cannot execute from status {record.status!r}.")
        record.status = "executed"
        record.executed_at = self.now().isoformat()
        self._save(records)
        return record

    def _expire_if_needed(self, record: ApprovalRecord, records: dict[str, ApprovalRecord]) -> None:
        if record.status in {"pending", "approved"} and self.now() >= datetime.fromisoformat(record.expires_at):
            record.status = "expired"
            self._save(records)
            raise PolicyViolation(f"Approval {record.approval_id!r} has expired; run a fresh broker review.")

    def _get(self, approval_id: str) -> tuple[ApprovalRecord, dict[str, ApprovalRecord]]:
        records = self._load()
        try:
            return records[approval_id], records
        except KeyError as exc:
            raise PolicyViolation(f"Approval {approval_id!r} was not found.") from exc

    def _load(self) -> dict[str, ApprovalRecord]:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return {key: ApprovalRecord(**value) for key, value in data.get("approvals", {}).items()}

    def _save(self, records: dict[str, ApprovalRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "approvals": {key: asdict(value) for key, value in records.items()}}
        fd, temporary = tempfile.mkstemp(prefix=".approvals-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")
