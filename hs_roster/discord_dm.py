"""Deliver the roster PDF to a single recipient via a Discord DM.

Uses the Discord REST API directly (no gateway / persistent bot process):
  1. POST /users/@me/channels {recipient_id} -> DM channel id
  2. POST /channels/<id>/messages  (multipart: payload_json + the PDF)
The bot must share a guild with the recipient for Discord to allow the DM.
"""
from __future__ import annotations
import json
from pathlib import Path

import httpx

API = "https://discord.com/api/v10"


def open_dm_channel(token: str, recipient_id: str, http: httpx.Client) -> str:
    r = http.post(
        f"{API}/users/@me/channels",
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
        content=json.dumps({"recipient_id": str(recipient_id)}),
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Open DM failed: HTTP {r.status_code} {r.text[:300]}")
    return r.json()["id"]


def send_roster_dm(
    token: str,
    recipient_id: str,
    pdf_path: str | Path,
    content: str,
    *,
    timeout: float = 60.0,
) -> None:
    """Send `content` + the PDF attachment to the recipient's DM."""
    pdf_path = Path(pdf_path)
    with httpx.Client(timeout=timeout) as http:
        channel_id = open_dm_channel(token, recipient_id, http)
        payload = {
            "content": content,
            "attachments": [{"id": 0, "filename": pdf_path.name}],
        }
        files = {
            "payload_json": (None, json.dumps(payload), "application/json"),
            "files[0]": (pdf_path.name, pdf_path.read_bytes(), "application/pdf"),
        }
        r = http.post(
            f"{API}/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {token}"},
            files=files,
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Send DM failed: HTTP {r.status_code} {r.text[:300]}")
