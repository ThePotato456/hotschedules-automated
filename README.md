# HotSchedules Daily Roster → Discord

Fetches the current day's HotSchedules **Roster Report**, renders it to a PDF
(the real report page, not a rebuilt table), publishes that PDF to
`files.example.com`, and DMs it (file + link) to one Discord recipient.
Runs weekdays at 08:00 as a systemd service on an Arch Linux server.

## Pipeline

```
login (httpx)  ->  render report page to PDF (Playwright Chromium)
               ->  upload to files.example.com (/api/public/uploads)
               ->  Discord DM: PDF attachment + hosted link
```

See `docs/API_RESEARCH.md` for the reverse-engineered HotSchedules API, the login
flow, and the delivery contracts. Project status/plan: `ACTIONPLAN.md`.

## Layout

```
hs_roster/
  client.py      HotSchedules login + authenticated JSON client
  roster.py      fetch + join the day's shifts into roster rows
  pdf.py         render the report page to PDF via Playwright
  publish.py     upload the PDF to the public file host
  discord_dm.py  DM the PDF + link to one recipient (Discord REST)
  config.py      secrets/env loading
  main.py        entrypoint: login -> PDF -> upload -> DM
deploy/          setup + systemd units (Arch)
```

## Setup (Arch server)

```bash
git clone <repo> /opt/hotschedules-automation
cd /opt/hotschedules-automation
./deploy/setup.sh                 # venv + deps + Playwright Chromium
$EDITOR secrets.env               # fill in real values (see below)
./.venv/bin/python -m hs_roster.main 2026-10-27   # test a known date
sudo ./deploy/install-service.sh  # install + enable the 08:00 Mon–Fri timer
```

If headless Chromium won't launch, install its libs:
```bash
sudo pacman -S --needed nss nspr atk at-spi2-core libcups libdrm \
  libxcomposite libxdamage libxrandr libxkbcommon mesa pango cairo alsa-lib
```

### secrets.env

Copy `secrets.env.example` → `secrets.env` (git-ignored) and set:

| Key | What |
|---|---|
| `HS_USERNAME` / `HS_PASSWORD` | HotSchedules login |
| `DISCORD_BOT_TOKEN` | A **dedicated** bot (Discord Developer Portal). Invite it to a server you also belong to, or it can't DM you. |
| `DISCORD_RECIPIENT_ID` | Your Discord user ID (Developer Mode → right-click name → Copy User ID) |
| `HS_STORE_TZ` *(opt)* | Store timezone; default `America/Chicago` |
| `UPLOADER_BASE_URL` *(opt)* | File host; default `https://files.example.com` |
| `PUBLISH_ENABLED` *(opt)* | Upload the PDF to the file host (needed for a link). Default `true` |
| `DM_INCLUDE_LINK` *(opt)* | Show the hosted link in the DM text. Default `true` |
| `DM_ATTACH_PDF` *(opt)* | Attach the PDF file to the DM. Default `true` |

Booleans accept `true/false`, `1/0`, `yes/no`, `on/off`. E.g. to send only the
link (no attachment): `DM_ATTACH_PDF=false`. To attach the PDF but not upload it
anywhere: `PUBLISH_ENABLED=false` (the DM then has no link).

## Run manually

```bash
./.venv/bin/python -m hs_roster.main            # today's roster (store tz)
./.venv/bin/python -m hs_roster.main 2026-10-27 # a specific date
```

## Service management

```bash
systemctl list-timers hotschedules-roster.timer      # next run
sudo systemctl start hotschedules-roster.service     # run now
journalctl -u hotschedules-roster.service -n 50      # logs
```

## Notes & caveats

- **Timezone:** the timer uses the *system* timezone. Make sure it matches the
  store, or edit the time in `deploy/hotschedules-roster.timer`.
- **Link lifetime:** the file host is a temporary uploader — links are random
  UUIDs that expire after ~24h. The DM always attaches the PDF itself, so the
  roster survives even after the link expires.
- **Unofficial API:** HotSchedules' internal endpoints may change without notice.
- Credentials and the session cookie jar are never committed (`.gitignore`).
