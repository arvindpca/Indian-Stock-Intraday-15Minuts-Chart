#!/usr/bin/env python3
"""Nifty 500 + NIFTY 50 index ka 5-minute data Angel One SmartAPI se (default 180 din).

Repo ke existing angel_client.get_client() se login hota hai (GitHub Secrets:
ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET). Koi order nahi lagta,
credentials print nahi hote. Jo file pehle se hai (cache se) wo dobara download nahi hoti.
"""
import csv
import io
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from angel_client import get_client  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
DATA_DIR = ROOT / "data" / "nifty500_5m"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DAYS = max(30, min(int(os.getenv("BACKTEST_DAYS", "180")), 200))
CHUNK_DAYS = 25
REQUEST_GAP = 1.1
INSTRUMENT_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
NIFTY500_URLS = [
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
]
NIFTY50_TOKEN = "99926000"


def nifty500_symbols():
    for url in NIFTY500_URLS:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            r.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(r.text)))
            syms = [row["Symbol"].strip().upper() for row in rows if row.get("Symbol")]
            if len(syms) > 400:
                return syms
        except Exception as e:
            print(f"Nifty 500 list fail: {url} ({e})", flush=True)
    print("Nifty 500 list nahi mili - stock_names.txt use kar rahe hain", flush=True)
    return [s.strip().upper().replace(".NS", "") for s in (ROOT / "stock_names.txt").read_text().splitlines() if s.strip()]


def fetch(client, token, start, end):
    rows, cursor, failed = {}, start, 0
    while cursor < end:
        stop = min(cursor + timedelta(days=CHUNK_DAYS), end)
        params = {"exchange": "NSE", "symboltoken": str(token), "interval": "FIVE_MINUTE",
                  "fromdate": cursor.strftime("%Y-%m-%d %H:%M"), "todate": stop.strftime("%Y-%m-%d %H:%M")}
        for attempt in range(2):
            try:
                resp = client.getCandleData(params)
                if not resp or not resp.get("status"):
                    raise RuntimeError(str((resp or {}).get("message", "empty"))[:150])
                for row in resp.get("data") or []:
                    if len(row) >= 6:
                        dt = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=IST)
                        rows[dt.astimezone(IST).strftime("%Y-%m-%d %H:%M")] = [float(x) for x in row[1:6]]
                break
            except Exception as e:
                msg = str(e).lower()
                if attempt == 0 and any(k in msg for k in ("rate", "too many", "403", "access")):
                    print("rate limit - 60s ruk rahe hain", flush=True)
                    time.sleep(60)
                    continue
                failed += 1
                break
        cursor = stop
        time.sleep(REQUEST_GAP)
    return rows, failed


def save(path, rows):
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for k in sorted(rows):
            w.writerow([k] + rows[k])


def main():
    missing = [x for x in ["ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET"] if not os.getenv(x)]
    if missing:
        raise SystemExit("Missing GitHub secrets: " + ", ".join(missing))
    master = requests.get(INSTRUMENT_URL, timeout=60).json()
    tokens = {str(i.get("symbol", "")).upper(): str(i.get("token", ""))
              for i in master if str(i.get("exch_seg", "")).upper() in ("NSE", "NSE_CM")}
    symbols = nifty500_symbols()
    max_sym = int(os.getenv("MAX_SYMBOLS", "0") or 0)
    if max_sym:
        symbols = symbols[:max_sym]
    end = datetime.now(IST).replace(second=0, microsecond=0)
    start = end - timedelta(days=DAYS)
    print(f"DOWNLOAD | {len(symbols)} stocks + NIFTY50 | {DAYS} din", flush=True)
    client = get_client()

    nifty_path = DATA_DIR / "NIFTY50_5m.csv"
    rows, _ = fetch(client, NIFTY50_TOKEN, start, end)
    if rows:
        save(nifty_path, rows)
    print(f"NIFTY50 candles: {len(rows)}", flush=True)

    ok = skipped = failed = 0
    for n, sym in enumerate(symbols, 1):
        path = DATA_DIR / f"{sym}_5m.csv"
        if path.exists() and path.stat().st_size > 10000:
            skipped += 1
            continue
        token = tokens.get(f"{sym}-EQ")
        if not token:
            failed += 1
            continue
        rows, _ = fetch(client, token, start, end)
        if rows:
            save(path, rows)
            ok += 1
        else:
            failed += 1
        if n % 25 == 0:
            print(f"PROGRESS {n}/{len(symbols)} | new={ok} cached={skipped} failed={failed}", flush=True)
    print(f"DONE | new={ok} cached={skipped} failed={failed}", flush=True)


if __name__ == "__main__":
    main()
