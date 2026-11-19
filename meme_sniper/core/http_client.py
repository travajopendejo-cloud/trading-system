"""Small JSON HTTP helper. Failures become typed errors and never include secrets."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any


class TemporaryFetchError(Exception):
    """Rate limit, timeout, or server error. The caller should try again later."""


class PermanentFetchError(Exception):
    """The server rejected the request. Retrying the same URL will not help."""


def classify_http_status(status: int) -> str:
    """Return 'ok', 'temporary', or 'permanent' for an HTTP status code."""
    if status == 429 or status >= 500:
        return "temporary"
    if status >= 400:
        return "permanent"
    return "ok"


class HttpJson:
    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = timeout

    async def get_json(self, url: str) -> Any:
        return await asyncio.to_thread(self._request, url, None)

    async def post_json(self, url: str, payload: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self._request, url, payload)

    def _request(self, url: str, payload: dict[str, Any] | None) -> Any:
        data = None if payload is None else json.dumps(payload).encode()
        headers = {
            "Accept": "application/json",
            "User-Agent": "trading-system-meme-sniper/1.0",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            # Do not chain the original error: its text can contain the request URL,
            # and a Helius URL includes the API key.
            kind = classify_http_status(exc.code)
            if kind == "temporary":
                raise TemporaryFetchError(f"HTTP {exc.code}") from None
            raise PermanentFetchError(f"HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise TemporaryFetchError(str(reason)) from None
        try:
            return json.loads(body.decode() or "null")
        except json.JSONDecodeError as exc:
            raise TemporaryFetchError("Response was not JSON") from exc
