"""Read-only raw-frame capture probe for the DhanHQ v2 market-feed WebSocket.

PURPOSE: definitively determine whether Dhan v2 concatenates MULTIPLE response
packets into a single WebSocket binary frame, and learn the exact `msg_len`
advance semantics — so the parser fix in ws_feed_v2.py is grounded in real
bytes, not assumption.

Works off-hours: on subscribe, Dhan sends a prev-close (code 6) packet per
instrument; if they arrive concatenated in one recv() the multi-packet
hypothesis is proven.

PAPER/DATA ONLY. Read-only market-data subscription; places no orders.
"""
from __future__ import annotations

import asyncio
import json
import struct
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import websockets

from algotrader.data.token_manager import get_valid_token, _load_env

IST = ZoneInfo("Asia/Kolkata")
WSS = "wss://api-feed.dhan.co"

# (sec_id, segment) — a dozen liquid instruments
INSTR = [
    ("13", "IDX_I"), ("25", "IDX_I"),
    ("2885", "NSE_EQ"), ("1333", "NSE_EQ"), ("1594", "NSE_EQ"),
    ("11536", "NSE_EQ"), ("4963", "NSE_EQ"), ("3045", "NSE_EQ"),
    ("1660", "NSE_EQ"), ("5900", "NSE_EQ"), ("1922", "NSE_EQ"),
    ("11483", "NSE_EQ"),
]

# code -> known fixed packet size (from verified struct formats)
KNOWN = {2: 16, 4: 50, 5: 12, 6: 16, 8: 162, 50: 10, 3: 112}
NAME = {2: "TICKER", 3: "MKT_DEPTH", 4: "QUOTE", 5: "OI", 6: "PREV_CLOSE",
        7: "STATUS", 8: "FULL", 50: "DISCONNECT"}


def walk(frame: bytes) -> list[dict]:
    """Walk a frame by the 8-byte header's msg_len field; report each packet."""
    out, off, n = [], 0, len(frame)
    while off + 8 <= n:
        code, msg_len, seg = struct.unpack_from("<BHB", frame, off)
        sec_id = struct.unpack_from("<I", frame, off + 4)[0]
        known = KNOWN.get(code)
        out.append({
            "off": off, "code": code, "name": NAME.get(code, "?"),
            "msg_len": msg_len, "seg": seg, "sec_id": sec_id,
            "known_size": known,
            "msg_len_eq_known": (known == msg_len) if known else None,
        })
        # advance: prefer known fixed size; fall back to msg_len
        step = known if known else msg_len
        if not step or step <= 0:
            out.append({"off": off, "ERROR": "zero/neg step", "msg_len": msg_len})
            break
        off += step
    out.append({"final_off": off, "frame_len": n, "exact_consume": off == n})
    return out


async def main(duration: float = 30.0):
    tok = get_valid_token()
    cid = _load_env().get("DHAN_CLIENT_ID", "")
    if not tok or not cid:
        print("NO TOKEN/CLIENT_ID", file=sys.stderr)
        return 1
    url = f"{WSS}?version=2&token={tok}&clientId={cid}&authType=2"

    raw_path = f"reports/ws_shadow/raw_capture_{datetime.now(IST):%Y%m%dT%H%M%S}.jsonl"
    fr_count = 0
    multi = 0
    convention_samples = []

    async with websockets.connect(url, ping_interval=None, ping_timeout=None,
                                  close_timeout=5) as ws:
        sub = {
            "RequestCode": 17,  # Quote
            "InstrumentCount": len(INSTR),
            "InstrumentList": [
                {"ExchangeSegment": seg, "SecurityId": sid} for sid, seg in INSTR
            ],
        }
        await ws.send(json.dumps(sub))
        print(f"subscribed {len(INSTR)} instruments (Quote mode); capturing {duration}s")

        loop = asyncio.get_event_loop()
        end = loop.time() + duration
        with open(raw_path, "w") as fh:
            while loop.time() < end:
                try:
                    data = await asyncio.wait_for(ws.recv(), timeout=end - loop.time())
                except asyncio.TimeoutError:
                    break
                except Exception as e:
                    print("recv err:", type(e).__name__, e); break
                if not isinstance(data, bytes):
                    print("TEXT frame:", str(data)[:200]); continue
                fr_count += 1
                w = walk(data)
                pkts = [p for p in w if "code" in p]
                if len(pkts) > 1:
                    multi += 1
                fh.write(json.dumps({"hex": data.hex(), "walk": w}) + "\n")
                # learn msg_len convention from known packets
                for p in pkts:
                    if p.get("known_size") and p["msg_len"] not in (s["msg_len"] for s in convention_samples if s.get("code") == p["code"]):
                        convention_samples.append(p)
                if fr_count <= 12:
                    summ = ", ".join(f"{p['name']}(ml={p['msg_len']},known={p['known_size']},sec={p['sec_id']})" for p in pkts)
                    tail = w[-1]
                    print(f"frame#{fr_count} len={len(data)} packets={len(pkts)} "
                          f"exact_consume={tail.get('exact_consume')} :: {summ}")

    print(f"\n=== SUMMARY ===")
    print(f"frames={fr_count} multi_packet_frames={multi}")
    print(f"raw saved to {raw_path}")
    print(f"\nmsg_len convention (known packets):")
    for s in convention_samples[:12]:
        print(f"  {s['name']}: msg_len_field={s['msg_len']} known_size={s['known_size']} "
              f"equal={s['msg_len_eq_known']}")
    return 0


if __name__ == "__main__":
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    raise SystemExit(asyncio.run(main(dur)))
