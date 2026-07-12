from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class SlackDelivery:
    approval_id: str
    channel_id: str
    status: str
    attempted_at: str
    message_ts: str | None = None
    message_link: str | None = None
    retry_count: int = 0
    error: str | None = None


class JsonDeliveryLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, delivery: SlackDelivery) -> None:
        rows = []
        if self.path.exists():
            rows = json.loads(self.path.read_text(encoding="utf-8")).get("deliveries", [])
        rows.append(asdict(delivery))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".deliveries-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "deliveries": rows}, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def delivery_attempt(approval_id: str, channel_id: str, **values) -> SlackDelivery:
    return SlackDelivery(
        approval_id=approval_id,
        channel_id=channel_id,
        attempted_at=datetime.now(timezone.utc).isoformat(),
        **values,
    )
