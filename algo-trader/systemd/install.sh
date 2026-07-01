#!/usr/bin/env bash
# Install/refresh algo-trader PAPER-ONLY systemd timers (system-level).
# Idempotent: copies units to /etc/systemd/system, reloads, enables + starts timers.
#   sudo bash systemd/install.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST=/etc/systemd/system
UNITS=(
  algo-trader-token.service algo-trader-token.timer
  algo-trader-paper.service algo-trader-paper.timer
  algo-trader-eod-report.service algo-trader-eod-report.timer
  algo-trader-eod-watchdog.service algo-trader-eod-watchdog.timer
  algo-trader-ws-shadow.service algo-trader-ws-shadow.timer
  algo-trader-optionchain.service algo-trader-optionchain.timer
  algo-trader-maxpain.service algo-trader-maxpain.timer
)
TIMERS=(
  algo-trader-token.timer algo-trader-paper.timer
  algo-trader-eod-report.timer algo-trader-eod-watchdog.timer
  algo-trader-ws-shadow.timer algo-trader-optionchain.timer
  algo-trader-maxpain.timer
)

for u in "${UNITS[@]}"; do
  install -m 0644 "$SRC/$u" "$DEST/$u"
  echo "installed $u"
done

systemctl daemon-reload
for t in "${TIMERS[@]}"; do
  systemctl enable --now "$t"
  echo "enabled+started $t"
done

echo "--- next fire times ---"
systemctl list-timers 'algo-trader-*' --all --no-pager
