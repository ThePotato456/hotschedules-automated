"""Cached roster print page. Run with python -m hs_roster.web."""
from __future__ import annotations

import hmac
import logging
import os
import threading
import time
import re
from dataclasses import dataclass
from datetime import date, datetime, time as day_time, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from flask import Flask, Response, abort, redirect, render_template, request, send_file, url_for
from waitress import serve

from .cache import acquire_roster, prune_cache, roster_path, valid_pdf
from .config import get_cache_dir, load_env_file
from .links import roster_page_url, roster_pdf_url

log = logging.getLogger("hs_roster.web")


@dataclass(frozen=True)
class WebConfig:
    token: str
    public_url: str
    cache_dir: Path
    timezone: str = "America/Chicago"
    host: str = "127.0.0.1"
    port: int = 3010
    ready_time: day_time = day_time(7, 45)
    retention_days: int = 30
    shortcut_name: str = "PrintPDF"

    @classmethod
    def from_env(cls) -> "WebConfig":
        load_env_file()
        token = os.environ.get("ROSTER_WEB_TOKEN", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", token):
            raise ValueError("Set ROSTER_WEB_TOKEN to a random URL-safe token of at least 32 characters")
        base = os.environ.get("ROSTER_WEB_PUBLIC_URL", "https://example.com/roster").rstrip("/")
        parsed = urlsplit(base)
        valid_path = not parsed.path or re.fullmatch(r"(?:/[A-Za-z0-9_-]+)+", parsed.path)
        if (parsed.scheme not in ("http", "https") or not parsed.netloc or not valid_path
                or parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise ValueError("ROSTER_WEB_PUBLIC_URL must be an absolute HTTP(S) URL with an optional simple path prefix")
        zone = os.environ.get("HS_STORE_TZ", "America/Chicago")
        ZoneInfo(zone)
        port = int(os.environ.get("ROSTER_WEB_PORT", "3010"))
        if not 1 <= port <= 65535:
            raise ValueError("Port must be 1..65535")
        ready = day_time.fromisoformat(os.environ.get("ROSTER_READY_TIME", "07:45"))
        if ready.tzinfo is not None:
            raise ValueError("ROSTER_READY_TIME must be a store-local time without an offset")
        return cls(
            token=token, public_url=base,
            cache_dir=get_cache_dir(), timezone=zone,
            host=os.environ.get("ROSTER_WEB_HOST", "127.0.0.1"), port=port,
            ready_time=ready,
            shortcut_name=os.environ.get("APPLE_SHORTCUT_NAME", "PrintPDF").strip() or "PrintPDF",
        )

    def today(self) -> date:
        return datetime.now(ZoneInfo(self.timezone)).date()


def refresh_due(config: WebConfig, now: datetime) -> bool:
    path = roster_path(config.cache_dir, now.date())
    if not valid_pdf(path):
        return now.time() >= config.ready_time
    modified = datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo(config.timezone))
    # Refresh once the store-local readiness time arrives.
    ready = datetime.combine(now.date(), config.ready_time, ZoneInfo(config.timezone))
    return modified < ready <= now


def refresh_loop(config: WebConfig, stop: threading.Event) -> None:
    retry_at = 0.0
    startup = True
    while not stop.is_set():
        try:
            now = datetime.now(ZoneInfo(config.timezone))
            prune_cache(config.cache_dir, now.date(), config.retention_days)
            if time.monotonic() >= retry_at and (startup or refresh_due(config, now)):
                acquire_roster(config.cache_dir, now.date())
                retry_at = 0.0
                startup = False
        except Exception:
            # Keep the last good PDF available and retry without blocking HTTP.
            log.exception("Roster refresh failed; retrying in five minutes")
            retry_at = time.monotonic() + 300
        stop.wait(30)


def create_app(config: WebConfig) -> Flask:
    prefix = urlsplit(config.public_url).path.rstrip("/")
    app = Flask(__name__, static_url_path=f"{prefix}/static")

    @app.after_request
    def headers(response: Response) -> Response:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        return response

    def authorize(token: str) -> None:
        if not hmac.compare_digest(token.encode(), config.token.encode()):
            abort(404)

    def requested_date(value: str) -> date:
        try:
            target = date.fromisoformat(value)
            if target.isoformat() != value:
                abort(404)
        except ValueError:
            abort(404)
        today = config.today()
        if not today - timedelta(days=config.retention_days - 1) <= target <= today:
            abort(404)
        return target

    @app.get(f"{prefix}/all/<token>/")
    def archive(token: str):
        authorize(token)
        today = config.today()
        rosters = [
            {"date": day, "url": roster_page_url(config.public_url, token, day)}
            for offset in range(config.retention_days)
            if valid_pdf(roster_path(config.cache_dir, day := today - timedelta(days=offset)))
        ]
        return render_template(
            "archive.html", rosters=rosters, retention_days=config.retention_days,
            today_url=roster_page_url(config.public_url, token, today),
        )

    @app.get(f"{prefix}/<value>/<token>/")
    def daily(token: str, value: str):
        authorize(token)
        target = requested_date(value)
        today = config.today()
        path = roster_path(config.cache_dir, target)
        available = valid_pdf(path)
        pdf_url = roster_pdf_url(config.public_url, token, target)
        shortcut = "shortcuts://run-shortcut?" + urlencode({
            "name": config.shortcut_name, "input": "text", "text": pdf_url,
        })
        response = app.make_response((render_template(
            "print.html", target=target, today=today,
            available=available, shortcut=shortcut, pdf_url=pdf_url,
            shortcut_name=config.shortcut_name, retention_days=config.retention_days,
            updated=datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo(config.timezone)).strftime("%b %d, %I:%M %p %Z") if available else None,
        ), 200 if available else 503))
        if not available:
            response.headers["Retry-After"] = "60"
        return response

    @app.get(f"{prefix}/<value>/<token>/pdf")
    def pdf(token: str, value: str):
        authorize(token)
        target = requested_date(value)
        path = roster_path(config.cache_dir, target)
        if not valid_pdf(path):
            abort(404)
        # No generation here. Date-specific URL prevents midnight from changing the selected PDF.
        return send_file(path, mimetype="application/pdf", as_attachment=False,
                         download_name=path.name, conditional=False, etag=False)

    # Keep previously sent links working, while giving them a canonical endpoint.
    @app.get(f"{prefix}/<token>/")
    def legacy_index(token: str):
        authorize(token)
        if "date" in request.args:
            target = requested_date(request.args["date"])
            return redirect(url_for("daily", value=target.isoformat(), token=token))
        return redirect(url_for("archive", token=token))

    @app.get(f"{prefix}/<token>/rosters/<value>.pdf")
    def legacy_pdf(token: str, value: str):
        authorize(token)
        target = requested_date(value)
        return redirect(url_for("pdf", value=target.isoformat(), token=token))

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = WebConfig.from_env()
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(config)
    stop = threading.Event()
    worker = threading.Thread(target=refresh_loop, args=(config, stop), daemon=True)
    worker.start()
    log.info("Print server listening on %s:%d; cache %s", config.host, config.port, config.cache_dir)
    try:
        serve(app, host=config.host, port=config.port, threads=4)
    finally:
        stop.set()
        worker.join(timeout=10)


if __name__ == "__main__":
    main()
