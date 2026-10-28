"""Daily roster job entrypoint: login -> render PDF -> publish -> Discord DM.

Run:  python -m hs_roster.main            # today's roster (store tz)
      python -m hs_roster.main 2026-10-27 # a specific date (for testing)
      python -m hs_roster.main clean      # delete all of the bot's DMs
"""
from __future__ import annotations
import logging
import sys
from datetime import date, datetime, timezone

from .cache import acquire_roster, cached_shift_count, prune_cache, roster_path, valid_pdf
from .config import get_delivery_config, get_cache_dir
from .roster import today_in_store_tz
from .publish import publish_pdf
from .discord_dm import send_roster_dm, clear_dms
from .links import roster_page_url

log = logging.getLogger("hs_roster")


def _human_date(d: date) -> str:
    return d.strftime("%a, %m/%d/%y")


def run(target: date | None = None) -> int:
    cfg = get_delivery_config()
    if target is None:
        target = today_in_store_tz()
    cache_dir = get_cache_dir()
    out = roster_path(cache_dir, target)
    if valid_pdf(out):
        shift_count = cached_shift_count(out)
        log.info("using prepared roster %s", target)
    else:
        out, shift_count = acquire_roster(cache_dir, target)


    web = cfg["roster_web"]
    print_url = roster_page_url(web["public_url"], web["token"], target) if web else None
    pub = None
    if print_url:
        log.info("using the date-specific print server link — skipping public upload")
    elif cfg["publish_enabled"]:
        pub = publish_pdf(out, base_url=cfg["uploader_base"])
        log.info("published -> %s (expires %s)", pub.public_url, pub.expires_at)
    else:
        log.info("publish disabled (PUBLISH_ENABLED=false) — skipping upload")

    summary = (
        "Daily roster ready." if shift_count is None else
        f"{shift_count} shift(s) scheduled." if shift_count else "No shifts scheduled."
    )
    embed: dict = {
        "title": f"Roster — {_human_date(target)}",
        "description": summary,
        "color": 0x0069AA,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "HotSchedules daily roster"},
    }
    if print_url:
        embed["url"] = print_url
        embed["fields"] = [{"name": "Print", "value": f"[Print roster]({print_url})"}]
    elif pub and cfg["dm_include_link"]:
        embed["fields"] = [{"name": "Download", "value": f"[Open roster PDF]({pub.public_url})"}]
        embed["footer"] = {"text": f"Link expires {pub.expires_at[:10]}"}

    attachment = out if cfg["dm_attach_pdf"] else None
    if attachment is None and not (pub and cfg["dm_include_link"]) and not print_url:
        log.warning("DM has neither PDF attachment nor link — embed only")
    send_roster_dm(cfg["token"], cfg["recipient_id"], embed=embed, pdf_path=attachment)
    log.info("DM delivered to %s", cfg["recipient_id"])
    prune_cache(cache_dir, today_in_store_tz())
    return 0


def run_cleanup() -> int:
    """Delete all of the bot's DMs to the configured recipient."""
    cfg = get_delivery_config()
    deleted, failed = clear_dms(cfg["token"], cfg["recipient_id"])
    log.info("cleanup: deleted %d message(s)%s", deleted,
             f", {failed} could not be deleted" if failed else "")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    argv = argv if argv is not None else sys.argv[1:]
    try:
        if argv and argv[0].lower() in ("clean", "clear", "cleanup"):
            return run_cleanup()
        target = date.fromisoformat(argv[0]) if argv else None
        return run(target)
    except Exception:
        log.exception("job failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
