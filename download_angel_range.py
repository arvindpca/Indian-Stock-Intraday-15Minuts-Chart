#!/usr/bin/env python3
"""Download NSE 5-minute candles from Angel One for a fixed date range.
Env: RANGE_START, RANGE_END (YYYY-MM-DD, IST), MAX_SYMBOLS (0 = all).
Writes data/angel_5m/<SYMBOL>_5m.csv (same format as download_angel_5m_6m.py)."""
import csv
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from angel_client import get_client

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "angel_5m"
OUT.mkdir(parents=True, exist_ok=True)
IST = ZoneInfo("Asia/Kolkata")
START = datetime.strptime(os.environ["RANGE_START"], "%Y-%m-%d").replace(hour=9, tzinfo=IST)
END = datetime.strptime(os.environ["RANGE_END"], "%Y-%m-%d").replace(hour=15, minute=30, tzinfo=IST)
MAX_SYMBOLS = int(os.getenv("MAX_SYMBOLS", "0") or 0)
CHUNK_DAYS, GAP = 30, 1.15
URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"


def main():
    tokens = {}
    for it in requests.get(URL, timeout=60).json():
        if str(it.get("exch_seg", "")).upper() in ("NSE", "NSE_CM") and str(it.get("symbol", "")).upper().endswith("-EQ"):
            tokens[str(it["symbol"]).upper()] = str(it["token"])
    names = []
    for line in (ROOT / "stock_names.txt").read_text(encoding="utf-8-sig").splitlines():
        s = line.strip().upper().replace(".NS", "")
        if s:
            names.append(s if s.endswith("-EQ") else s + "-EQ")
    names = list(dict.fromkeys(names))[: MAX_SYMBOLS or None]
    client = get_client()
    report, total = [], 0
    print(f"RANGE {START:%Y-%m-%d} -> {END:%Y-%m-%d} | symbols {len(names)}", flush=True)
    for n, sym in enumerate(names, 1):
        tok = tokens.get(sym)
        if not tok:
            report.append({"symbol": sym, "status": "NO_TOKEN", "rows": 0})
            continue
        rows, fails, cur = {}, 0, START
        while cur < END:
            stop = min(cur + timedelta(days=CHUNK_DAYS), END)
            params = {"exchange": "NSE", "symboltoken": tok, "interval": "FIVE_MINUTE",
                      "fromdate": cur.strftime("%Y-%m-%d %H:%M"), "todate": stop.strftime("%Y-%m-%d %H:%M")}
            for attempt in range(3):
                try:
                    r = client.getCandleData(params)
                    if not r or not r.get("status"):
                        raise RuntimeError(str((r or {}).get("message", "empty"))[:150])
                    for row in r.get("data") or []:
                        dt = datetime.fromisoformat(str(row[0])).astimezone(IST)
                        rows[dt.isoformat()] = {"symbol": sym[:-3], "time_ist": dt.isoformat(), "open": row[1],
                                                "high": row[2], "low": row[3], "close": row[4], "volume": row[5]}
                    break
                except Exception as exc:
                    msg = str(exc).lower()
                    wait = 60 if any(k in msg for k in ("rate", "403", "access", "too many")) else 5
                    if attempt == 2:
                        fails += 1
                        print(f"  {sym} {cur:%Y-%m-%d}: {str(exc)[:120]}", flush=True)
                    time.sleep(wait)
            cur = stop
            time.sleep(GAP)
        if rows:
            with (OUT / f"{sym[:-3]}_5m.csv").open("w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=["symbol", "time_ist", "open", "high", "low", "close", "volume"])
                w.writeheader()
                w.writerows(rows[k] for k in sorted(rows))
        total += len(rows)
        report.append({"symbol": sym, "status": "OK" if rows and not fails else ("PARTIAL" if rows else "FAILED"), "rows": len(rows)})
        if n % 10 == 0 or n == len(names):
            print(f"PROGRESS {n}/{len(names)} | candles {total}", flush=True)
    summ = {"start": f"{START:%Y-%m-%d}", "end": f"{END:%Y-%m-%d}", "symbols": len(names), "candles": total,
            **{s: sum(r["status"] == s for r in report) for s in ("OK", "PARTIAL", "FAILED", "NO_TOKEN")}}
    (ROOT / "data" / "range_download_summary.json").write_text(json.dumps(summ, indent=2))
    print(json.dumps(summ, indent=2))
    if total == 0:
        raise SystemExit("no candles downloaded")


if __name__ == "__main__":
    main()
