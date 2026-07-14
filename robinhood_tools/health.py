from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib import request

from .errors import PolicyViolation


HealthTransport = Callable[[dict[str, str], str], dict[str, Any]]


class SlackHealthWebApiNotifier:
    """Fixed-route health notifier for an independent local watchdog."""

    def __init__(self, *, bot_token: str, channel_id: str, transport: HealthTransport | None = None):
        if not bot_token.strip():
            raise PolicyViolation("SLACK_BOT_TOKEN is required for watchdog health alerts.")
        if not channel_id.startswith(("C", "G")):
            raise PolicyViolation("HEALTH_SLACK_CHANNEL_ID must be a fixed C... or G... channel ID.")
        self._token = bot_token
        self._channel_id = channel_id
        self._transport = transport or self._request

    @classmethod
    def from_values(cls, values: dict[str, str], *, transport: HealthTransport | None = None):
        return cls(
            bot_token=values.get("SLACK_BOT_TOKEN", os.environ.get("SLACK_BOT_TOKEN", "")),
            channel_id=values.get("HEALTH_SLACK_CHANNEL_ID", os.environ.get("HEALTH_SLACK_CHANNEL_ID", "")),
            transport=transport,
        )

    def send_health_alert(self, *, message: str) -> None:
        response = self._transport({"channel": self._channel_id, "text": message}, self._token)
        if response.get("ok") is not True:
            raise RuntimeError(f"Slack health alert failed ({response.get('error', 'unknown_error')}); fail closed.")

    @staticmethod
    def _request(payload: dict[str, str], token: str) -> dict[str, Any]:
        api_request = request.Request(
            "https://slack.com/api/chat.postMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with request.urlopen(api_request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
