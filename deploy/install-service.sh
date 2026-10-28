#!/usr/bin/env bash
# Install + enable the systemd service and timer. Run with sudo.
# Fills the @APPDIR@/@USER@ placeholders from the current checkout.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The user the job runs as: the owner of the repo (not root).
RUN_USER="${SUDO_USER:-$(stat -c '%U' "$REPO")}"
UNIT_DIR=/etc/systemd/system

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo." >&2
  exit 1
fi
if [ ! -x "$REPO/.venv/bin/python" ]; then
  echo "No venv found — run deploy/setup.sh first (as $RUN_USER)." >&2
  exit 1
fi

echo ">> repo:     $REPO"
echo ">> run user: $RUN_USER"

for unit in hotschedules-roster.service hotschedules-roster.timer hotschedules-web.service; do
  sed -e "s#@APPDIR@#$REPO#g" -e "s#@USER@#$RUN_USER#g" \
    "$REPO/deploy/$unit" > "$UNIT_DIR/$unit"
  echo ">> installed $UNIT_DIR/$unit"
done

# Timezone note: OnCalendar uses the system timezone. Ensure it matches the store
# (America/Chicago) or adjust the time in the .timer:  timedatectl set-timezone America/Chicago
echo ">> system timezone: $(timedatectl show -p Timezone --value 2>/dev/null || echo unknown)"

systemctl daemon-reload
systemctl enable --now hotschedules-roster.timer
echo ">> enabled. Upcoming runs:"
systemctl list-timers hotschedules-roster.timer --no-pager || true
echo ">> run once now to test:  sudo systemctl start hotschedules-roster.service"
echo ">> logs:                  journalctl -u hotschedules-roster.service -n 50 --no-pager"

# Opt in after configuring ROSTER_WEB_TOKEN and ROSTER_WEB_PUBLIC_URL.
# The web server warms its cache on startup; it runs independently of DM delivery.
echo ">> print server installed. After configuration, enable with:"
echo "   sudo systemctl enable --now hotschedules-web.service"
