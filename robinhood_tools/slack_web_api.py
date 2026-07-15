from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any
from urllib import parse, request

from .errors import PolicyViolation


SlackTransport = Callable[[str, dict[str, str], str], dict[str, Any]]


class SlackWebApiNotifier:
    """Fixed-channel Slack writer for post-trade paper summaries; it grants no trading authority."""

    def __init__(
        self, *, bot_token: str, allowed_channel_id: str, transport: SlackTransport | None = None,
    ):
        if not bot_token.strip():
            raise PolicyViolation("SLACK_BOT_TOKEN is required for paper trade summaries.")
        if not allowed_channel_id.startswith(("C", "G")):
            raise PolicyViolation("Paper trade summaries require a fixed C... or G... channel ID.")
        self._bot_token = bot_token
        self._allowed_channel_id = allowed_channel_id
        self._transport = transport or self._request

    @classmethod
    def from_values(
        cls, values: dict[str, str], *, transport: SlackTransport | None = None,
    ) -> SlackWebApiNotifier:
        return cls(
            bot_token=values.get("SLACK_BOT_TOKEN", ""),
            allowed_channel_id=values.get("SLACK_CHANNEL_ID", ""),
            transport=transport,
        )

    def send_approval(self, *, channel_id: str, message: str) -> dict:
        if channel_id != self._allowed_channel_id:
            raise PolicyViolation("Slack summary channel does not match SLACK_CHANNEL_ID.")
        try:
            response = self._transport(
                "chat.postMessage", {"channel": channel_id, "text": message}, self._bot_token,
            )
        except Exception as exc:
            raise RuntimeError("Slack paper-summary transport failed.") from exc
        if response.get("ok") is not True:
            raise RuntimeError(f"Slack paper summary failed ({response.get('error', 'unknown_error')}).")
        message_ts = str(response.get("ts", ""))
        return {"message_ts": message_ts, "message_link": response.get("permalink")}

    def _request(self, method: str, payload: dict[str, str], token: str) -> dict[str, Any]:
        body = parse.urlencode(payload).encode("utf-8")
        api_request = request.Request(
            f"https://slack.com/api/{method}",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with request.urlopen(api_request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))


class SlackWebApiReplyHost:
    """Minimal exact-thread Slack adapter; it exposes no trading operations."""

    def __init__(
        self,
        *,
        bot_token: str,
        user_token: str,
        allowed_channel_id: str,
        transport: SlackTransport | None = None,
    ):
        if not bot_token.strip():
            raise PolicyViolation("SLACK_BOT_TOKEN is required for the Slack reply monitor.")
        if not user_token.strip():
            raise PolicyViolation("SLACK_USER_TOKEN is required for public or private channel thread reads.")
        if not allowed_channel_id.startswith(("C", "G")):
            raise PolicyViolation("The Slack reply monitor requires a fixed C... or G... channel ID.")
        self._bot_token = bot_token
        self._user_token = user_token
        self._allowed_channel_id = allowed_channel_id
        self._transport = transport or self._request

    @classmethod
    def from_environment(cls, *, transport: SlackTransport | None = None) -> SlackWebApiReplyHost:
        return cls(
            bot_token=os.environ.get("SLACK_BOT_TOKEN", ""),
            user_token=os.environ.get("SLACK_USER_TOKEN", ""),
            allowed_channel_id=os.environ.get("SLACK_CHANNEL_ID", ""),
            transport=transport,
        )

    def replies(self, *, channel_id: str, parent_message_ts: str) -> list[dict]:
        self._require_channel(channel_id)
        response = self._call(
            "conversations.replies", {"channel": channel_id, "ts": parent_message_ts}, self._user_token,
        )
        messages = response.get("messages")
        if not isinstance(messages, list):
            raise RuntimeError("Slack conversations.replies returned no message list; fail closed.")
        return [
            {"message_ts": str(item.get("ts", "")), "text": str(item.get("text", ""))}
            for item in messages
            if (
                isinstance(item, dict)
                and str(item.get("ts", "")) != parent_message_ts
                and not item.get("bot_id")
                and item.get("subtype") != "bot_message"
            )
        ]

    def acknowledge(self, *, channel_id: str, parent_message_ts: str, message: str) -> None:
        self._require_channel(channel_id)
        self._call("chat.postMessage", {
            "channel": channel_id,
            "thread_ts": parent_message_ts,
            "text": message,
        }, self._bot_token)

    def _require_channel(self, channel_id: str) -> None:
        if channel_id != self._allowed_channel_id:
            raise PolicyViolation("Slack reply monitor channel does not match SLACK_CHANNEL_ID.")

    def _call(self, method: str, payload: dict[str, str], token: str) -> dict[str, Any]:
        try:
            response = self._transport(method, payload, token)
        except Exception as exc:
            raise RuntimeError(f"Slack {method} transport failed; fail closed.") from exc
        if response.get("ok") is not True:
            error = str(response.get("error", "unknown_error"))
            raise RuntimeError(f"Slack {method} failed ({error}); fail closed.")
        return response

    def _request(self, method: str, payload: dict[str, str], token: str) -> dict[str, Any]:
        body = parse.urlencode(payload).encode("utf-8")
        api_request = request.Request(
            f"https://slack.com/api/{method}",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with request.urlopen(api_request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
