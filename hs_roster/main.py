"""Daily roster job entrypoint: login -> render PDF -> publish -> Discord DM.

Run:  python -m hs_roster.main            # today's roster (store tz)
      python -m hs_roster.main 2026-10-27 # a specific date (for testing)
"""
from __future__ import annotations
import logging
import sys
import tempfile
from datetime import date
from pathlib import Path

from .client import HSClient
from .config import get_delivery_config
from .roster import fetch_roster, today_in_store_tz
from .pdf import render_roster_pdf
from .publish import publish_pdf
from .discord_dm import send_roster_dm

log = logging.getLogger("hs_roster")


def _human_date(d: date) -> str:
    return d.strftime("%a, %m/%d/%y")


def run(target: date | None = None) -> int:
    cfg = get_delivery_config()
    client = HSClient().login()
    try:
        if target is None:
            target = today_in_store_tz()
        # Roster rows: for the DM summary and the empty-day note.
        _, rows = fetch_roster(client, target)
        log.info("roster %s: %d shift(s)", target, len(rows))

        out = Path(tempfile.gettempdir()) / f"roster_{target.isoformat()}.pdf"
        render_roster_pdf(client, target, out)
        log.info("rendered PDF -> %s (%d bytes)", out, out.stat().st_size)
    finally:
        client.close()

    pub = None
    if cfg["publish_enabled"]:
        pub = publish_pdf(out, base_url=cfg["uploader_base"])
        log.info("published -> %s (expires %s)", pub.public_url, pub.expires_at)
    else:
        log.info("publish disabled (PUBLISH_ENABLED=false) — skipping upload")

    summary = f"{len(rows)} shift(s) scheduled." if rows else "No shifts scheduled."
    lines = [f"**Roster — {_human_date(target)}**", summary]
    if pub and cfg["dm_include_link"]:
        lines.append(pub.public_url)
        lines.append(f"_(link expires {pub.expires_at[:10]})_")
    content = "\n".join(lines)

    attachment = out if cfg["dm_attach_pdf"] else None
    if attachment is None and not (pub and cfg["dm_include_link"]):
        log.warning("DM has neither PDF attachment nor link — sending text only")
    send_roster_dm(cfg["token"], cfg["recipient_id"], content, pdf_path=attachment)
    log.info("DM delivered to %s", cfg["recipient_id"])
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    argv = argv if argv is not None else sys.argv[1:]
    target = date.fromisoformat(argv[0]) if argv else None
    try:
        return run(target)
    except Exception:
        log.exception("roster job failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
