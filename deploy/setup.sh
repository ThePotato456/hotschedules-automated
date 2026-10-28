#!/usr/bin/env bash
# One-time setup for the Arch server: venv, dependencies, Playwright Chromium.
# Run from anywhere; it resolves the repo root itself.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

PY="${PYTHON:-python3}"
echo ">> repo: $REPO"
echo ">> creating venv (.venv) with $PY"
"$PY" -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

# Keep Playwright's browser inside the repo so the systemd unit finds it
# regardless of the service user's HOME.
export PLAYWRIGHT_BROWSERS_PATH="$REPO/.playwright"
echo ">> installing Playwright Chromium into $PLAYWRIGHT_BROWSERS_PATH"
./.venv/bin/python -m playwright install chromium

cat <<'NOTE'

>> If headless Chromium fails to launch on Arch, install its shared libs:
     sudo pacman -S --needed nss nspr atk at-spi2-core libcups libdrm \
       libxcomposite libxdamage libxrandr libxkbcommon mesa pango cairo alsa-lib
NOTE

if [ ! -f secrets.env ]; then
  cp secrets.env.example secrets.env
  echo ">> created secrets.env from example — EDIT IT with your real values."
else
  echo ">> secrets.env already exists — leaving it untouched."
fi

echo ">> setup complete. Test with:  ./.venv/bin/python -m hs_roster.main 2026-10-27"
echo ">> then install the service:   sudo ./deploy/install-service.sh"
