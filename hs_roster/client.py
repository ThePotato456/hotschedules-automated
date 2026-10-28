"""Authenticated HotSchedules HTTP client.

Login flow (reverse-engineered — see docs/API_RESEARCH.md §2):
  1. GET /hs/login.hs  -> redirects to /hs/login.jsp?svrid=<N>...; page carries a
     hidden ``_csrf`` token and seeds initial cookies.
  2. POST /hs/prelogin.hs?svrid=<N>&timestamp=<ms> with form fields
     _csrf, username, fake_pass=Password, password, rememberMe=1.
  3. Server 302 -> GET /hs/login.hs?authToken=... which sets the session cookies
     (JSESSIONID, Client-Pod, ApplicationGatewayAffinity, hs_user, hs_username,
     remember_me). The cookie jar then authenticates all /hs/spring/* calls.
"""
from __future__ import annotations
import re
import time
from typing import Any

import httpx

from .config import BASE_URL, get_credentials

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)

_CSRF_RE = re.compile(r'name=["\']_csrf["\'][^>]*value=["\']([^"\']+)["\']')
_CSRF_RE2 = re.compile(r'value=["\']([^"\']+)["\'][^>]*name=["\']_csrf["\']')
_SVRID_RE = re.compile(r"svrid=(\d+)")


class HotSchedulesError(RuntimeError):
    pass


class HSClient:
    def __init__(self, timeout: float = 30.0):
        self.http = httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        )
        self.logged_in = False

    # -- auth -------------------------------------------------------------
    def login(self) -> "HSClient":
        user, pwd = get_credentials()

        # 1. login.hs is a tiny shell that meta-refreshes to login.jsp?svrid=<N>.
        #    httpx won't follow a <meta refresh>, so grab svrid and fetch the form.
        r0 = self.http.get(f"{BASE_URL}/login.hs")
        r0.raise_for_status()
        svrid_m = _SVRID_RE.search(r0.text)
        svrid = svrid_m.group(1) if svrid_m else "1"

        # 2. Load the actual login form; captures _csrf + seeds cookies.
        r = self.http.get(
            f"{BASE_URL}/login.jsp",
            params={"svrid": svrid, "timestamp": int(time.time() * 1000)},
        )
        r.raise_for_status()
        html = r.text
        m = _CSRF_RE.search(html) or _CSRF_RE2.search(html)
        if not m:
            raise HotSchedulesError("Could not find _csrf token on the login page.")
        csrf = m.group(1)

        # 3. POST credentials to prelogin.hs.
        ts = int(time.time() * 1000)
        resp = self.http.post(
            f"{BASE_URL}/prelogin.hs",
            params={"svrid": svrid, "timestamp": ts},
            data={
                "_csrf": csrf,
                "username": user,
                "fake_pass": "Password",
                "password": pwd,
                "rememberMe": "1",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": str(r.url),
                "Origin": "https://app.hotschedules.com",
            },
        )
        resp.raise_for_status()

        # 4. Still on a login page => bad credentials or a challenge.
        if "login" in str(resp.url).lower() and "menuParser" not in str(resp.url):
            raise HotSchedulesError(
                f"Login did not complete (landed on {resp.url}). "
                "Check credentials, or a bot/Cloudflare challenge may be blocking HTTP login."
            )
        self.logged_in = True
        return self

    # -- api --------------------------------------------------------------
    def get_json(self, path: str, params: dict | None = None) -> Any:
        """GET a /hs/<path> endpoint and return parsed JSON."""
        url = path if path.startswith("http") else f"{BASE_URL}/{path.lstrip('/')}"
        p = dict(params or {})
        p.setdefault("_", int(time.time() * 1000))
        r = self.http.get(url, params=p, headers={"X-Requested-With": "XMLHttpRequest"})
        r.raise_for_status()
        return r.json()

    def verify(self) -> bool:
        """Phase 1 gate: a core authenticated endpoint returns 200 + JSON."""
        data = self.get_json("spring/scheduling/bootstrap")
        return isinstance(data, dict) and "dayParts" in data

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "HSClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
