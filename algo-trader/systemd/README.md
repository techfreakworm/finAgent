# algo-trader systemd timers (PAPER-ONLY scheduling)

Durable, reboot-persistent scheduling for the paper-trading system. Mirrors the
VPS convention (`claude-soma-*` timers, system-level). All times are IST
(system timezone = Asia/Kolkata).

| Unit | Fires (IST) | Does |
|---|---|---|
| `algo-trader-token.timer` | Mon–Fri 08:45 | Mint a fresh 24h Dhan token (data auth) |
| `algo-trader-paper.timer` | Mon–Fri 09:00 | Run the intraday PAPER session (breadth accounts + 0DTE straddle on expiry days); persists trades; self-publishes EOD ~15:35; hard cap 15:45 |
| `algo-trader-eod-report.timer` | Mon–Fri 15:45 | Generate + publish the daily EOD P&L report from the store (suspenders if the session died early) |
| `algo-trader-maxpain.timer` | Mon–Fri 15:50 | Log the pre-registered max-pain / PCR / OI-wall signals + outcomes for the session to `reports/maxpain_pcr_eval.jsonl` (LOGS ONLY — no trade decision, read-only over the collected chain) |
| `algo-trader-eod-watchdog.timer` | Mon–Fri 15:55 | Verify the report exists + alert on any lingering open paper positions |

`Persistent=true` on every timer → a run missed during downtime fires when the
box comes back. No `Restart=` on the session service → a clean end-of-session
exit is expected, not retried.

## Install / update (idempotent)

    sudo bash systemd/install.sh

This copies the unit files to `/etc/systemd/system/`, reloads the daemon, and
enables + starts the timers. Re-run after editing any unit. Holidays / no-signal
days are handled by the strategies themselves (they no-op and the EOD report
shows zero trades) — the timers fire every weekday regardless.

PAPER-ONLY: the system has a structural live-order lockout; these timers run the
paper executor exclusively. No unit places real orders.
