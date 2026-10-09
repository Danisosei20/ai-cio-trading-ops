from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib import parse, request

from .errors import PolicyViolation


SlackTransport = Callable[[str, dict[str, str], str], dict[str, Any]]
SlackUploadTransport = Callable[[str, dict[str, str], str, bytes, str, str], dict[str, Any]]


class SlackWebApiNotifier:
    """Fixed-channel Slack writer for post-trade paper summaries; it grants no trading authority."""

    def __init__(
        self, *, bot_token: str, allowed_channel_id: str,
        transport: SlackTransport | None = None, upload_transport: SlackUploadTransport | None = None,
    ):
        if not bot_token.strip():
            raise PolicyViolation("SLACK_BOT_TOKEN is required for paper trade summaries.")
        if not allowed_channel_id.startswith(("C", "G")):
            raise PolicyViolation("Paper trade summaries require a fixed C... or G... channel ID.")
        self._bot_token = bot_token
        self._allowed_channel_id = allowed_channel_id
        self._transport = transport or self._request
        self._upload_transport = upload_transport or self._upload_request

    @classmethod
    def from_values(
        cls, values: dict[str, str], *, transport: SlackTransport | None = None,
        upload_transport: SlackUploadTransport | None = None,
    ) -> SlackWebApiNotifier:
        return cls(
            bot_token=values.get("SLACK_BOT_TOKEN", ""),
            allowed_channel_id=values.get("SLACK_CHANNEL_ID", ""),
            transport=transport,
            upload_transport=upload_transport,
        )

    def send_approval(
        self,
        *,
        channel_id: str,
        message: str,
        image_path: str | Path | None = None,
        image_alt_text: str | None = None,
    ) -> dict:
        if channel_id != self._allowed_channel_id:
            raise PolicyViolation("Slack summary channel does not match SLACK_CHANNEL_ID.")
        try:
            response = self._transport(
                "chat.postMessage", {"channel": channel_id, "text": message}, self._bot_token,
            )
            if response.get("ok") is not True:
                raise RuntimeError(f"Slack paper summary failed ({response.get('error', 'unknown_error')}).")
            result = {"message_ts": str(response.get("ts", "")), "message_link": response.get("permalink")}
            if image_path is not None and result["message_ts"]:
                image_result = self._upload_image(
                    channel_id=channel_id,
                    image_path=image_path,
                    thread_ts=result["message_ts"],
                    comment=image_alt_text,
                )
                result.update(image_result)
            return result
        except Exception as exc:
            raise RuntimeError("Slack paper-summary transport failed.") from exc

    def send_image(
        self,
        *,
        channel_id: str,
        image_path: str | Path,
        message: str = "",
        image_alt_text: str | None = None,
    ) -> dict:
        return self.send_approval(
            channel_id=channel_id,
            message=message,
            image_path=image_path,
            image_alt_text=image_alt_text,
        )

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

    def _upload_image(
        self,
        *,
        channel_id: str,
        image_path: str | Path,
        thread_ts: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        path = Path(image_path)
        if not path.is_file():
            raise PolicyViolation(f"Image path {path} does not exist or is not a file.")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        payload: dict[str, str] = {
            "channels": channel_id,
            "thread_ts": thread_ts,
            "filename": path.name,
            "title": path.stem,
        }
        if comment is not None and comment.strip():
            payload["initial_comment"] = comment.strip()
        with path.open("rb") as handle:
            data = handle.read()
        response = self._upload_transport(
            "files.upload", payload, self._bot_token, data, path.name, content_type,
        )
        if response.get("ok") is not True:
            raise RuntimeError(f"Slack file upload failed ({response.get('error', 'unknown_error')}).")
        file_raw = response.get("file") if isinstance(response, dict) else None
        file_info = file_raw if isinstance(file_raw, dict) else {}
        return {
            "file_id": str(file_info.get("id", "")),
            "file_permalink": file_info.get("permalink"),
        }

    def _upload_request(
        self,
        method: str,
        payload: dict[str, str],
        token: str,
        file_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> dict[str, Any]:
        boundary = uuid.uuid4().hex
        body = self._build_multipart_body(payload, file_bytes, filename, content_type, boundary)
        api_request = request.Request(
            f"https://slack.com/api/{method}",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        with request.urlopen(api_request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _build_multipart_body(
        payload: dict[str, str],
        file_bytes: bytes,
        filename: str,
        content_type: str,
        boundary: str,
    ) -> bytes:
        chunks: list[bytes] = []
        for key, value in payload.items():
            chunks.extend([
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            ])
        chunks.extend([
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ])
        return b"".join(chunks)
