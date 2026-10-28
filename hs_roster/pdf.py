"""Render the HotSchedules Roster Report page to PDF with Playwright Chromium.

Strategy: authenticate with httpx (hs_roster.client), copy the resulting session
cookies into a Playwright browser context, open newReport.hs#roster/<date>, wait
for the roster to render, then page.pdf(). The report page's own print stylesheet
drops the app nav/sidebar, yielding a clean 1-page document.
"""
from __future__ import annotations
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from .client import HSClient
from .config import BASE_URL

REPORT_URL = "https://app.hotschedules.com/hs/newReport.hs#roster/{date}"


def _cookies_for_playwright(client: HSClient) -> list[dict]:
    out = []
    for c in client.http.cookies.jar:
        out.append({
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path or "/",
            "secure": bool(c.secure),
        })
    return out


def render_roster_pdf(client: HSClient, target: date, out_path: str | Path,
                      *, wait_ms: int = 45000) -> Path:
    """Render the roster report for ``target`` to ``out_path`` (PDF). Requires a
    logged-in HSClient. Raises if the roster never renders."""
    from playwright.sync_api import sync_playwright  # lazy import

    out_path = Path(out_path)
    url = REPORT_URL.format(date=target.isoformat())
    cookies = _cookies_for_playwright(client)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            context.add_cookies(cookies)
            page = context.new_page()
            page.goto(url, wait_until="load", timeout=wait_ms)
            # Wait until the roster content is actually on the page.
            page.wait_for_function(
                "() => { const t = document.body ? document.body.innerText : '';"
                " return t.includes('Roster Report For') && t.includes('Hrs'); }",
                timeout=wait_ms,
            )
            page.wait_for_timeout(2000)  # settle layout/fonts
            page.pdf(
                path=str(out_path),
                print_background=True,
                prefer_css_page_size=True,
                format="Letter",
                margin={"top": "0.4in", "bottom": "0.4in",
                        "left": "0.4in", "right": "0.4in"},
            )
        finally:
            browser.close()
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("PDF was not produced.")
    return out_path
