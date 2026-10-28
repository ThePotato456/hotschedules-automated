"""Deliver the roster PDF to a single recipient via a Discord DM.

Uses the Discord REST API directly (no gateway / persistent bot process):
  1. POST /users/@me/channels {recipient_id} -> DM channel id
  2. POST /channels/<id>/messages  (multipart: payload_json + the PDF)
The bot must share a guild with the recipient for Discord to allow the DM.
"""
from __future__ import annotations
import json
import time
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
    content: str,
    pdf_path: str | Path | None = None,
    *,
    timeout: float = 60.0,
) -> None:
    """DM `content` to the recipient, optionally attaching the PDF at `pdf_path`."""
    with httpx.Client(timeout=timeout) as http:
        channel_id = open_dm_channel(token, recipient_id, http)
        url = f"{API}/channels/{channel_id}/messages"
        auth = {"Authorization": f"Bot {token}"}
        if pdf_path is None:
            r = http.post(url, headers={**auth, "Content-Type": "application/json"},
                          content=json.dumps({"content": content}))
        else:
            pdf_path = Path(pdf_path)
            payload = {"content": content, "attachments": [{"id": 0, "filename": pdf_path.name}]}
            files = {
                "payload_json": (None, json.dumps(payload), "application/json"),
                "files[0]": (pdf_path.name, pdf_path.read_bytes(), "application/pdf"),
            }
            r = http.post(url, headers=auth, files=files)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Send DM failed: HTTP {r.status_code} {r.text[:300]}")


def _bot_user_id(token: str, http: httpx.Client) -> str:
    r = http.get(f"{API}/users/@me", headers={"Authorization": f"Bot {token}"})
    if r.status_code != 200:
        raise RuntimeError(f"Fetch bot identity failed: HTTP {r.status_code} {r.text[:200]}")
    return r.json()["id"]


def clear_dms(token: str, recipient_id: str, *, timeout: float = 60.0) -> tuple[int, int]:
    """Delete every message THIS bot sent in the recipient's DM.

    Discord only allows a bot to delete its own messages, so the recipient's own
    messages are left untouched. Returns (deleted, failed).
    """
    with httpx.Client(timeout=timeout) as http:
        auth = {"Authorization": f"Bot {token}"}
        me = _bot_user_id(token, http)
        channel_id = open_dm_channel(token, recipient_id, http)
        deleted = failed = 0
        before: str | None = None
        while True:
            params = {"limit": 100}
            if before:
                params["before"] = before
            r = http.get(f"{API}/channels/{channel_id}/messages", headers=auth, params=params)
            if r.status_code != 200:
                raise RuntimeError(f"List messages failed: HTTP {r.status_code} {r.text[:200]}")
            msgs = r.json()
            if not msgs:
                break
            for m in msgs:
                if m["author"]["id"] != me:
                    continue
                dr = _delete_with_backoff(http, channel_id, m["id"], auth)
                if dr:
                    deleted += 1
                else:
                    failed += 1
            before = msgs[-1]["id"]
            if len(msgs) < 100:
                break
        return deleted, failed


def _delete_with_backoff(http: httpx.Client, channel_id: str, message_id: str, auth: dict) -> bool:
    """Delete one message, waiting out Discord's per-channel rate limit (429)."""
    url = f"{API}/channels/{channel_id}/messages/{message_id}"
    for _ in range(5):
        r = http.delete(url, headers=auth)
        if r.status_code in (200, 204):
            return True
        if r.status_code == 429:
            retry = float(r.headers.get("Retry-After", "1")) or 1.0
            time.sleep(retry + 0.25)
            continue
        if r.status_code == 404:  # already gone
            return True
        return False
    return False
