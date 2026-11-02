from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass

from bazaar_intel.config import HTTP_TIMEOUT, USER_AGENT


@dataclass(frozen=True)
class FetchResult:
    url: str
    ok: bool
    status_code: int | None
    body: bytes
    final_url: str | None
    error: str | None
    method: str


def fetch(url: str, *, timeout: int = HTTP_TIMEOUT, method: str = "GET") -> FetchResult:
    request = urllib.request.Request(
        url,
        method=method,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/json,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = b"" if method == "HEAD" else response.read()
            code = getattr(response, "status", None) or response.getcode()
            return FetchResult(url, 200 <= int(code) < 400, int(code), body, response.geturl(), None, method)
    except urllib.error.HTTPError as exc:
        body = exc.read() if method != "HEAD" else b""
        return FetchResult(url, False, int(exc.code), body, exc.geturl(), str(exc), method)
    except Exception as exc:  # noqa: BLE001 - network failures must be recorded, not invented
        return FetchResult(url, False, None, b"", None, f"{type(exc).__name__}: {exc}", method)


def decode_body(body: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1256"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")
