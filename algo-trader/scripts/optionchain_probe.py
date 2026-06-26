"""Read-only feasibility probe for Dhan REST /v2/optionchain.

Verifies the source algo-brain identified for FORWARD pinned-strike data
collection (the unblock for clean P2/P3 + a measured slippage model): does
/optionchain return, per strike by NAMED expiry (no re-anchoring), the real
bid/ask + IV + greeks + OI we need? Confirms field names/format before we
propose standing up a collector.

PAPER/DATA ONLY. Read-only; places no orders. Respects the 1-req/3s limit.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from algotrader.data.token_manager import get_valid_token, _load_env  # noqa: E402

API = "https://api.dhan.co/v2"
NIFTY_SCRIP, SEG = 13, "IDX_I"


def main():
    tok = get_valid_token()
    cid = _load_env().get("DHAN_CLIENT_ID", "")
    if not tok or not cid:
        print("NO TOKEN/CLIENT_ID"); return 1
    h = {"access-token": tok, "client-id": cid, "Content-Type": "application/json"}

    # 1) expiry list
    r = requests.post(f"{API}/optionchain/expirylist",
                      json={"UnderlyingScrip": NIFTY_SCRIP, "UnderlyingSeg": SEG},
                      headers=h, timeout=20)
    print("expirylist HTTP", r.status_code)
    try:
        exps = r.json().get("data", r.json())
    except ValueError:
        print("non-JSON:", r.text[:300]); return 1
    print("expiries (first 5):", exps[:5] if isinstance(exps, list) else exps)
    if not isinstance(exps, list) or not exps:
        print("no expiries returned"); return 1
    expiry = exps[0]

    time.sleep(3.1)  # rate limit: 1 req / 3s / unique key

    # 2) option chain for nearest expiry
    r = requests.post(f"{API}/optionchain",
                      json={"UnderlyingScrip": NIFTY_SCRIP, "UnderlyingSeg": SEG, "Expiry": expiry},
                      headers=h, timeout=30)
    print(f"\noptionchain({expiry}) HTTP", r.status_code)
    j = r.json()
    if r.status_code != 200:
        print("body:", str(j)[:400]); return 1
    data = j.get("data", {})
    spot = data.get("last_price") or data.get("underlying")
    oc = data.get("oc", {})
    print("underlying last_price:", spot, "| #strikes:", len(oc))
    if not oc:
        print("empty chain (off-hours?) raw keys:", list(data.keys())); return 0

    strikes = sorted(float(k) for k in oc.keys())
    atm = min(strikes, key=lambda s: abs(s - (spot or strikes[len(strikes)//2])))
    near = [s for s in strikes if abs(s - atm) <= 200][:9]
    print(f"\nATM≈{atm}; showing {len(near)} strikes around ATM:")
    # show field structure from one node
    sample = oc[f"{atm:.6f}"] if f"{atm:.6f}" in oc else oc[list(oc.keys())[0]]
    ce_fields = list(sample.get("ce", {}).keys())
    print("CE node fields:", ce_fields)
    print(f"\n{'strike':>8} {'type':>3} {'last':>8} {'bid':>8} {'ask':>8} {'iv':>6} {'delta':>6} {'theta':>8} {'oi':>10} {'vol':>9}")
    for s in near:
        node = oc.get(f"{s:.6f}", {})
        for t in ("ce", "pe"):
            n = node.get(t, {}) or {}
            g = n.get("greeks", {}) or {}
            print(f"{s:>8.0f} {t.upper():>3} {str(n.get('last_price','')):>8} "
                  f"{str(n.get('top_bid_price','')):>8} {str(n.get('top_ask_price','')):>8} "
                  f"{str(n.get('implied_volatility','')):>6} {str(g.get('delta','')):>6} "
                  f"{str(g.get('theta','')):>8} {str(n.get('oi','')):>10} {str(n.get('volume','')):>9}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
