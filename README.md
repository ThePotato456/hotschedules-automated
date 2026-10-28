# HotSchedules Daily Roster → Discord

Fetches the current day's HotSchedules **Roster Report**, renders the *real*
report page to a PDF (not a rebuilt table), optionally publishes that PDF to a
public file host, and DMs it to one Discord recipient as a rich embed plus the
PDF attachment. Runs weekdays at 08:00 as a systemd service on an Arch Linux
server.

## Features

- **Faithful PDF** — renders `newReport.hs#roster/<date>` with headless Chromium,
  so the DM contains the exact report page (print stylesheet, no app chrome).
- **Rich Discord embed** — title, shift count, color accent, timestamp, and a
  download link when publishing is on; the PDF follows as a second message.
- **Configurable delivery** — toggle the file upload, the link, and the
  attachment independently via `secrets.env` (see below).
- **Timezone-correct** — "today" is computed in the store's timezone, so the
  08:00 run always targets the right day regardless of host clock.
- **Self-cleaning** — `clean` deletes the bot's own DMs, rate-limit aware.
- **Unattended** — a systemd timer (`Persistent=true`) runs it Mon–Fri and
  catches up after downtime; no credentials ever leave the box except the DM.

## Pipeline

```
login (httpx)  ->  render report page to PDF (Playwright Chromium)
               ->  [optional] upload to the file host (/api/public/uploads)
               ->  Discord DM: embed first, then the PDF attachment
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
  discord_dm.py  DM the embed + PDF, and the clean (DM-delete) helper
  config.py      secrets/env loading + delivery toggles
  main.py        entrypoint: login -> PDF -> [publish] -> DM; also `clean`
deploy/
  setup.sh                     venv + deps + Playwright Chromium (repo-local)
  install-service.sh           install + enable the systemd unit/timer
  hotschedules-roster.service  oneshot job, triggered by the timer
  hotschedules-roster.timer    OnCalendar=Mon..Fri 08:00, Persistent=true
```

## Requirements

- Python 3.11+ (developed on 3.14)
- Dependencies (`requirements.txt`): `httpx`, `playwright`, `tzdata`
- Headless Chromium (installed by `deploy/setup.sh` into `<repo>/.playwright`)
- A dedicated Discord bot that shares a server with the recipient
- Linux for the service (Arch targeted); the pipeline itself runs on any OS

## Setup (Arch server)

```bash
git clone https://github.com/ThePotato456/hotschedules-automated.git \
  /opt/hotschedules-automation
cd /opt/hotschedules-automation
./deploy/setup.sh                 # venv + deps + Playwright Chromium
$EDITOR secrets.env               # fill in real values (see below)
./.venv/bin/python -m hs_roster.main 2026-10-27   # test a known date
sudo ./deploy/install-service.sh  # install + enable the 08:00 Mon–Fri timer
```

If headless Chromium won't launch (a `libXYZ.so: cannot open shared object`
error), install its libs:
```bash
sudo pacman -S --needed nss nspr atk at-spi2-core libcups libdrm \
  libxcomposite libxdamage libxrandr libxkbcommon mesa pango cairo alsa-lib
```

> On Arch, Playwright prints `BEWARE: your OS is not officially supported …
> downloading fallback build for ubuntu24.04-x64`. That's expected — the Ubuntu
> build runs fine on Arch.

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
| `DM_INCLUDE_LINK` *(opt)* | Show the hosted link in the embed. Default `true` |
| `DM_ATTACH_PDF` *(opt)* | Attach the PDF file to the DM. Default `true` |

Booleans accept `true/false`, `1/0`, `yes/no`, `on/off`. **No inline comments** —
put any comment on its own line (systemd's `EnvironmentFile` treats everything
after `=` as the value).

Common combinations:

| Goal | Settings |
|---|---|
| Embed + link + PDF (default) | *(all unset)* |
| Embed + PDF, no link/upload | `PUBLISH_ENABLED=false` |
| Embed + link only, no file | `DM_ATTACH_PDF=false` |
| Embed only (text) | `PUBLISH_ENABLED=false` + `DM_ATTACH_PDF=false` |

## What the DM looks like

Two messages are sent, embed first so it renders above the file:

1. **Embed** — title `Roster — <Day, MM/DD/YY>`, description `N shift(s)
   scheduled.`, a HotSchedules-orange accent, a send timestamp, and a footer. When
   publishing is on, a **Download → [Open roster PDF]** field is added and the
   footer shows the link expiry date.
2. **PDF** — the rendered report, attached as its own message (when
   `DM_ATTACH_PDF=true`).

## Run manually

```bash
./.venv/bin/python -m hs_roster.main            # today's roster (store tz)
./.venv/bin/python -m hs_roster.main 2026-10-27 # a specific date
./.venv/bin/python -m hs_roster.main clean      # delete all of the bot's DMs
```

> On Windows the venv's Python is `./.venv/Scripts/python.exe`; the `bin/` path
> above is the Linux/macOS layout.

`clean` (aliases: `clear`, `cleanup`) deletes every message **this bot** has sent
to `DISCORD_RECIPIENT_ID` (the roster embeds and PDFs). Discord only lets a bot
delete its *own* messages, so your side of the conversation is never touched. It
reads Discord's rate-limit headers and paces itself, so clearing a long history
won't trip the limiter.

## Service management

The timer fires the one-shot service Mon–Fri at 08:00. `install-service.sh`
enables it, so it starts on every boot:

```bash
systemctl is-enabled hotschedules-roster.timer     # -> enabled
systemctl list-timers hotschedules-roster.timer    # next scheduled run
sudo systemctl start hotschedules-roster.service   # run once now (blocks)
systemctl status  hotschedules-roster.service      # last run result
journalctl -u hotschedules-roster.service -n 50    # logs
```

- Only the **timer** is enabled for boot; the **service** is `Type=oneshot` and
  is triggered by the timer (or a manual `start`) — it is intentionally not
  enabled on its own.
- `Persistent=true`: if the box is off at 08:00, the job runs shortly after the
  next boot instead of being skipped.
- To change the schedule, edit `deploy/hotschedules-roster.timer`
  (`OnCalendar=`), then re-run `sudo ./deploy/install-service.sh`.

## Troubleshooting

- **`Executable doesn't exist at …/ms-playwright/…`** — Chromium isn't installed.
  Run `./deploy/setup.sh` (installs it into `<repo>/.playwright`, which the app
  and the service both use automatically).
- **Chromium launches but errors on a missing `.so`** — install the shared libs
  (the `pacman` line above).
- **`ZoneInfoNotFoundError` / tz errors** — the `tzdata` dep provides the IANA
  database; make sure `requirements.txt` is installed in the venv.
- **Discord `Cannot send messages to this user` / DM fails** — the bot must share
  a server with the recipient, and the recipient's privacy settings must allow
  DMs from server members.
- **Wrong day in the roster** — "today" uses `HS_STORE_TZ`; the systemd *timer*
  uses the system timezone, so align the host clock (`timedatectl set-timezone
  America/Chicago`) or edit the timer's time.
- **Unofficial API** — HotSchedules' internal endpoints may change without
  notice; a login or roster failure usually means a changed endpoint or an
  expired password.

## Security

- `secrets.env` and the session cookie jar (`*cookies*.txt`) are git-ignored and
  never committed. Only `secrets.env.example` is tracked.
- The only data that leaves the server is the roster PDF: optionally to the file
  host, and in the Discord DM to the single configured recipient.
- The systemd unit runs as the repo owner (not root) with light hardening
  (`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`).
