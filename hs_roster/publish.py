"""Publish the roster PDF to files.example.com via the download-bot public
uploader.

Contract (verified):
  POST <base>/api/public/uploads   multipart form field ``file``
  -> 201 {"ok": true, "publication": {"public_url": "...", "uuid": "...",
          "generated_filename": "...", "size_bytes": N, "expires_at": "..."}}
Notes: filenames are randomized UUIDs; links expire (default 24h TTL); a ~30s
cooldown between uploads returns 429.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .client import USER_AGENT

DEFAULT_UPLOADER = "https://files.example.com"


@dataclass
class Publication:
    public_url: str
    uuid: str
    size_bytes: int
    expires_at: str


def publish_pdf(
    pdf_path: str | Path,
    base_url: str = DEFAULT_UPLOADER,
    *,
    retry_on_cooldown: bool = True,
    timeout: float = 60.0,
) -> Publication:
    pdf_path = Path(pdf_path)
    data = pdf_path.read_bytes()
    url = f"{base_url.rstrip('/')}/api/public/uploads"

    with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}) as http:
        for attempt in range(2):
            files = {"file": (pdf_path.name, data, "application/pdf")}
            r = http.post(url, files=files)
            if r.status_code == 429 and retry_on_cooldown and attempt == 0:
                # Respect the uploader cooldown, then retry once.
                wait = _retry_after_seconds(r, default=31)
                time.sleep(wait)
                continue
            if r.status_code != 201:
                raise RuntimeError(
                    f"Upload failed: HTTP {r.status_code} {r.text[:300]}"
                )
            body = r.json()
            pub = body.get("publication", {})
            if not pub.get("public_url"):
                raise RuntimeError(f"Upload response missing public_url: {body}")
            return Publication(
                public_url=pub["public_url"],
                uuid=pub.get("uuid", ""),
                size_bytes=pub.get("size_bytes", len(data)),
                expires_at=pub.get("expires_at", ""),
            )
    raise RuntimeError("Upload failed after cooldown retry (still 429).")


def _retry_after_seconds(resp: httpx.Response, default: int) -> int:
    ra = resp.headers.get("Retry-After")
    if ra and ra.isdigit():
        return int(ra)
    return default
