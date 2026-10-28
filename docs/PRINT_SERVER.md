# Roster print server

The Python server serves a print page and previously rendered PDFs. Print requests
read disk only: HotSchedules login and Chromium rendering run in a background
worker. The existing daily Discord job reuses the prepared PDF, generating a
fallback only when missing. When the web URL and token are configured, the Discord
embed title and its Print roster link both open that day's dedicated print page.
The print button passes that same day's protected PDF to PrintPDF. Public file
uploads are skipped in this mode; the optional Discord PDF attachment is unchanged.

Defaults:

- Acquire today's roster on every server startup.
- Refresh at 07:45 America/Chicago, before the existing 08:00 delivery.
- No hourly polling of HotSchedules. Overnight, the new day remains unavailable
  until 07:45 unless the server starts earlier that day.
- Retry failed refreshes after five minutes and keep the previous good PDF.
- Keep today plus the previous 29 calendar dates. No historical backfill; the
  archive grows as days are acquired. Weekends are included by the server.
- First startup needs time to log in and render; the page says it is not ready
  until the PDF exists. Acquisition failures cannot guarantee readiness.
- A date-specific print link always points to that day, including after midnight.

## Configure

Install updated dependencies with deploy/setup.sh or:

```bash
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Add the generated token and URL to secrets.env (one setting per line):

```dotenv
ROSTER_WEB_PUBLIC_URL=https://example.com/roster
ROSTER_WEB_TOKEN=YOUR_GENERATED_TOKEN
ROSTER_WEB_HOST=127.0.0.1
ROSTER_WEB_PORT=3010
ROSTER_CACHE_DIR=var/rosters
ROSTER_READY_TIME=07:45
APPLE_SHORTCUT_NAME=PrintPDF
HS_STORE_TZ=America/Chicago
```

Cache paths are relative to the repository unless absolute. Both services must
use the same directory and OS user. PDFs are employee data; var/ is git-ignored.
Tokens must be URL-safe, at least 32 characters. The token is a bearer credential:
anyone holding the full link can read retained PDFs. Do not share it or log
request URLs at the reverse proxy. HTTPS must be configured for phone access.
The server binds loopback by default and does not terminate TLS.

The worker checks the local clock every 30 seconds. Only startup, the daily
readiness time, and retries trigger acquisition. Requests never trigger it.

## Run and install

```bash
./.venv/bin/python -m hs_roster.web
sudo ./deploy/install-service.sh
sudo systemctl enable --now hotschedules-web.service
journalctl -u hotschedules-web.service -f
```

The installer installs the web unit but does not enable it before configuration.
The existing weekday Discord timer keeps its schedule.

## Caddy hosting

Keep the Python server on 127.0.0.1:3010 and let Caddy handle HTTPS.
The public URL remains https://example.com/roster.
This configuration assumes Caddy and Python run on the same host.

Import [deploy/roster.caddy](../deploy/roster.caddy) **inside your existing**
example.com site block in /etc/caddy/Caddyfile, adjusting the checkout path:

```caddyfile
example.com {
    import /opt/hotschedules-automation/deploy/roster.caddy

    # Keep your other routes here. An existing catch-all app should be
    # in an unqualified handle block so /roster/* gets its own handler.
    # handle {
    #     reverse_proxy 127.0.0.1:YOUR_OTHER_APP_PORT
    # }
}
```

The imported fragment contains:

```caddyfile
log_skip /roster/*

handle /roster/* {
    reverse_proxy 127.0.0.1:3010
}
```

Use handle to preserve /roster; handle_path strips it and would break this app's
routes. Caddy forwards the original URI to the Python server. log_skip requires
Caddy 2.8+ and excludes token-bearing roster paths from access logs. Ensure any
upstream CDN logging also avoids storing these private links.

Validate the complete configuration before reloading Caddy:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
```

Caddy manages the domain's HTTPS certificate. DNS and inbound ports 80/443 must
reach Caddy; port 3010 stays private. Caddy does not need filesystem access to
the cached PDFs because Python serves them over loopback.

References: [Caddy reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy),
[handle](https://caddyserver.com/docs/caddyfile/directives/handle),
[log_skip](https://caddyserver.com/docs/caddyfile/directives/log_skip).

The endpoints after `/roster` explicitly identify the archive or a specific day:

| Purpose | URL |
|---|---|
| Browse all retained rosters | `https://example.com/roster/all/YOUR_GENERATED_TOKEN/` |
| Bookmark today's roster | `https://example.com/roster/today/YOUR_GENERATED_TOKEN/` |
| Print page sent in the October 28 DM | `https://example.com/roster/2026-10-28/YOUR_GENERATED_TOKEN/` |
| PDF used by that page's print button | `https://example.com/roster/2026-10-28/YOUR_GENERATED_TOKEN/pdf` |

Open the archive URL in Safari to choose a day. Each daily page has a Print roster
button and an Open PDF link, without a date picker. Its date comes from the path;
query parameters cannot switch it to another day. It remains on that date after
midnight and returns 404 once outside the retention window. The public prefix
alone returns 404. The token still grants access to all retained dates; date paths
pin the content of a link rather than limiting the token's permissions.

Bookmark `/roster/today/YOUR_GENERATED_TOKEN/` for a permanent entry to today's
roster. It keeps that URL in the address bar and resolves the current store-local
date on each open or refresh. Its print button still uses a dated PDF URL, so an
already loaded page prints the date shown. If today's PDF is not ready, it shows
the pending state rather than serving yesterday's roster. The archive's Open
today's roster link uses this endpoint. Discord continues sending fixed-date links.

Discord's Print roster link opens the HTTPS daily page. Tap its Print roster
button to launch PrintPDF; the shortcut downloads the dated PDF directly from
the protected server. No browser sign-in session or public PDF uploader is needed.
The server must be reachable from the phone.

Previously sent `/roster/TOKEN/?date=YYYY-MM-DD` and
`/roster/TOKEN/rosters/YYYY-MM-DD.pdf` links redirect to their new dated endpoints.
The old `/roster/TOKEN/` entry redirects to the archive.

With the print server configured, `PUBLISH_ENABLED` and `DM_INCLUDE_LINK` apply
only to the legacy uploader fallback used when no print server is configured.
`DM_ATTACH_PDF` continues to control the separate Discord attachment.

## Apple Shortcut

The installed PrintPDF shortcut accepts Text and URLs, then:
1. Get Contents of URL (GET), using Shortcut Input.
2. Print, using Contents of URL from the previous action.

The print button passes its page's fixed-date PDF URL through
shortcuts://run-shortcut?name=PrintPDF&input=text&text=URL_ENCODED_PDF_URL.
iOS opens Shortcuts and presents printing. This does not silently print.

## Verification

Run .venv/bin/python -m unittest discover -s tests -v (Windows uses
.venv/Scripts/python.exe). Tests use synthetic data, temporary caches, and mocked
HotSchedules calls. They cover access control, date boundaries, missing/corrupt
PDFs, the complete Discord-page-shortcut-PDF link chain, fixed dates across
midnight, legacy redirects, archive links, atomic replacement, and refresh scheduling.
