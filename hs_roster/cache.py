"""Atomic roster PDF cache shared by the daily job and web server."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

from .client import HSClient
from .pdf import render_roster_pdf
from .roster import fetch_roster

log = logging.getLogger("hs_roster")


def roster_path(directory: Path, target: date) -> Path:
    return directory / f"roster_{target.isoformat()}.pdf"


def valid_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(5) == b"%PDF-"
    except FileNotFoundError:
        return False


def prune_cache(directory: Path, today: date, days: int = 30) -> None:
    """Keep today and the previous days-1 calendar dates."""
    cutoff = today - timedelta(days=days - 1)
    for path in directory.glob("roster_*.pdf"):
        try:
            target = date.fromisoformat(path.stem.removeprefix("roster_"))
        except ValueError:
            continue
        if target < cutoff:
            path.unlink(missing_ok=True)
            path.with_suffix(".json").unlink(missing_ok=True)


def acquire_roster(directory: Path, target: date) -> tuple[Path, int]:
    """Render to a unique temporary file; replace the previous PDF only on success."""
    directory.mkdir(parents=True, exist_ok=True)
    destination = roster_path(directory, target)
    descriptor, temporary = tempfile.mkstemp(prefix=".roster-", suffix=".pdf", dir=directory)
    os.close(descriptor)
    temporary = Path(temporary)
    client = HSClient()
    try:
        client.login()
        _, rows = fetch_roster(client, target)
        render_roster_pdf(client, target, temporary)
        if not valid_pdf(temporary):
            raise RuntimeError("Roster renderer did not produce a PDF")
        os.replace(temporary, destination)
        # Metadata is optional for serving/printing; only the DM summary uses it.
        metadata = temporary.with_suffix(".json")
        try:
            metadata.write_text(json.dumps({"shift_count": len(rows)}), encoding="utf-8")
            os.replace(metadata, destination.with_suffix(".json"))
        finally:
            metadata.unlink(missing_ok=True)
        log.info("cached roster %s: %d shifts", target, len(rows))
        return destination, len(rows)
    finally:
        client.close()
        temporary.unlink(missing_ok=True)


def cached_shift_count(path: Path) -> int | None:
    try:
        count = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))["shift_count"]
        return count if type(count) is int and count >= 0 else None
    except (OSError, ValueError, KeyError, TypeError):
        return None
