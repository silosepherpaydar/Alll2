from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

from bourse_intel.config import USER_AGENT


@dataclass
class HttpResult:
    url: str
    status: int | None
    body: bytes
    error: str
    blocked: bool

    @property
    def ok(self) -> bool:
        return self.error == "" and self.status is not None and 200 <= self.status < 300


def fetch(url: str, timeout: float = 20, accept: str = "*/*") -> HttpResult:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": accept},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return HttpResult(url, response.status, response.read(), "", False)
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        return HttpResult(url, exc.code, body, f"HTTP {exc.code}", False)
    except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
        reason = str(getattr(exc, "reason", exc))
        blocked = "timed out" in reason.lower() or "timeout" in reason.lower()
        return HttpResult(url, None, b"", reason, blocked)


def fetch_json(url: str, timeout: float = 30) -> tuple[HttpResult, dict | list | None]:
    result = fetch(url, timeout=timeout, accept="application/json")
    if not result.ok:
        return result, None
    try:
        return result, json.loads(result.body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        result.error = f"JSON نامعتبر: {exc}"
        return result, None
