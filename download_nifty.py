#!/usr/bin/env python3
"""Download NIFTY 50 index candles from Angel One: 5-minute (DAYS) and daily (400 days).
Writes data/nifty/NIFTY_5m.csv and data/nifty/NIFTY_1d.csv. Places no orders."""
import csv
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from angel_client import get_client

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "nifty"
OUT.mkdir(parents=True, exist_ok=True)
IST = ZoneInfo("Asia/Kolkata")
DAYS = int(os.getenv("BACKTEST_DAYS", "180")) + 5
INSTRUMENT_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"


def nifty_token():
    try:
        for x in requests.get(INSTRUMENT_URL, timeout=45).json():
            if str(x.get("exch_seg", "")).upper() == "NSE" and str(x.get("name", "")).upper() == "NIFTY" \
                    and str(x.get("instrumenttype", "")).upper() in ("AMXIDX", "INDEX", ""):
                return str(x["token"])
    except Exception as exc:
        print(f"instrument master lookup failed: {exc}")
    return "99926000"  # Angel's NIFTY 50 index token


def fetch(client, token, interval, start, end, chunk_days):
    rows, cur = {}, start
    while cur < end:
        stop = min(cur + timedelta(days=chunk_days), end)
        params = {"exchange": "NSE", "symboltoken": token, "interval": interval,
                  "fromdate": cur.strftime("%Y-%m-%d %H:%M"), "todate": stop.strftime("%Y-%m-%d %H:%M")}
        for attempt in range(3):
            try:
                r = client.getCandleData(params)
                if r and r.get("status"):
                    for row in r.get("data") or []:
                        rows[str(row[0])] = row
                    break
                raise RuntimeError(str((r or {}).get("message")))
            except Exception as exc:
                print(f"{interval} {cur.date()} attempt {attempt + 1}: {exc}")
                time.sleep(20 if attempt else 3)
        cur = stop
        time.sleep(1.2)
    return [rows[k] for k in sorted(rows)]


def save(path, rows):
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_ist", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    print(f"{path.name}: {len(rows)} candles")


def main():
    token = nifty_token()
    print(f"NIFTY token {token}")
    client = get_client()
    now = datetime.now(IST).replace(second=0, microsecond=0)
    start = now - timedelta(days=DAYS)
    if os.getenv("RANGE_START"):            # fixed historical window
        start = datetime.strptime(os.environ["RANGE_START"], "%Y-%m-%d").replace(tzinfo=IST) - timedelta(days=5)
        now = datetime.strptime(os.environ["RANGE_END"], "%Y-%m-%d").replace(hour=15, minute=30, tzinfo=IST)
    save(OUT / "NIFTY_5m.csv", fetch(client, token, "FIVE_MINUTE", start, now, 25))
    save(OUT / "NIFTY_1d.csv", fetch(client, token, "ONE_DAY", start - timedelta(days=120), now, 380))


if __name__ == "__main__":
    main()
