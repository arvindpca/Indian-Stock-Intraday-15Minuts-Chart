#!/usr/bin/env python3
"""Download six months of NSE 5-minute OHLCV using Angel One SmartAPI.
Requires GitHub Secrets: ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET.
Does not place orders and never prints credentials.
"""
import csv, json, os, time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from angel_client import get_client

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "angel_5m"
DATA_DIR.mkdir(parents=True, exist_ok=True)
IST = ZoneInfo("Asia/Kolkata")
DAYS = max(30, min(int(os.getenv("BACKTEST_DAYS", "180")), 190))
MAX_SYMBOLS = max(0, int(os.getenv("MAX_SYMBOLS", "0")))
CHUNK_DAYS = 25
REQUEST_GAP = 1.15
INSTRUMENT_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

def load_watchlist():
    names = []
    for line in (ROOT / "stock_names.txt").read_text(encoding="utf-8-sig").splitlines():
        s = line.strip().upper().replace(".NS", "")
        if s:
            names.append(s if s.endswith("-EQ") else s + "-EQ")
    return list(dict.fromkeys(names))

def main():
    required = ["ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET"]
    missing = [x for x in required if not os.getenv(x)]
    if missing:
        raise SystemExit("Missing GitHub Actions secrets: " + ", ".join(missing))
    print("Loading Angel One instrument master...", flush=True)
    res = requests.get(INSTRUMENT_URL, timeout=45)
    res.raise_for_status()
    instruments = res.json()
    tokens = {}
    for item in instruments:
        if str(item.get("exch_seg", "")).upper() in ("NSE_CM", "NSE"):
            sym = str(item.get("symbol", "")).upper()
            tok = str(item.get("token", ""))
            if sym.endswith("-EQ") and tok:
                tokens[sym] = tok
    watchlist = load_watchlist()
    if MAX_SYMBOLS:
        watchlist = watchlist[:MAX_SYMBOLS]
    start = datetime.now(IST).replace(second=0, microsecond=0) - timedelta(days=DAYS)
    end = datetime.now(IST).replace(second=0, microsecond=0)
    print(f"ANGEL 5M DOWNLOAD | symbols={len(watchlist)} | days={DAYS} | chunk={CHUNK_DAYS}d", flush=True)
    client = get_client()
    report = []
    total_rows = 0
    for n, symbol in enumerate(watchlist, 1):
        token = tokens.get(symbol)
        rows_by_time = {}
        errors = []
        if not token:
            report.append({"symbol": symbol, "token": "", "status": "NO_TOKEN", "rows": 0, "chunks_ok": 0, "chunks_failed": 0, "error": "Symbol not found in Angel NSE-EQ master"})
            continue
        cursor = start
        chunks_ok = chunks_failed = 0
        while cursor < end:
            stop = min(cursor + timedelta(days=CHUNK_DAYS), end)
            params = {
                "exchange": "NSE",
                "symboltoken": str(token),
                "interval": "FIVE_MINUTE",
                "fromdate": cursor.strftime("%Y-%m-%d %H:%M"),
                "todate": stop.strftime("%Y-%m-%d %H:%M"),
            }
            try:
                response = client.getCandleData(params)
                if not response or not response.get("status"):
                    raise RuntimeError(str((response or {}).get("message", "empty API response"))[:220])
                for row in response.get("data") or []:
                    if len(row) < 6:
                        continue
                    ts = str(row[0])
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=IST)
                        key = dt.astimezone(IST).isoformat()
                        vals = [float(row[i]) for i in range(1, 6)]
                        if all(v == v for v in vals):
                            rows_by_time[key] = {"symbol": symbol.replace("-EQ", ""), "time_ist": key, "open": vals[0], "high": vals[1], "low": vals[2], "close": vals[3], "volume": vals[4]}
                    except (ValueError, TypeError, OverflowError):
                        continue
                chunks_ok += 1
            except Exception as exc:
                chunks_failed += 1
                errors.append(f"{cursor.date()}..{stop.date()}: {str(exc)[:160]}")
                # Do not hammer the API if it reports a rate limit.
                if any(k in str(exc).lower() for k in ("rate", "403", "access denied", "too many")):
                    print(f"RATE LIMIT/ACCESS ERROR at {symbol}; cooling down 60s", flush=True)
                    time.sleep(60)
            cursor = stop
            time.sleep(REQUEST_GAP)
        rows = [rows_by_time[k] for k in sorted(rows_by_time)]
        if rows:
            out = DATA_DIR / f"{symbol.replace('-EQ','')}_5m.csv"
            with out.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        status = "OK" if rows and not chunks_failed else ("PARTIAL" if rows else "FAILED")
        report.append({"symbol": symbol, "token": token, "status": status, "rows": len(rows), "chunks_ok": chunks_ok, "chunks_failed": chunks_failed, "error": " | ".join(errors)[:900]})
        total_rows += len(rows)
        if n == 1 or n % 5 == 0 or n == len(watchlist):
            ok = sum(x["status"] == "OK" for x in report)
            print(f"PROGRESS {n}/{len(watchlist)} | fully_ok={ok} | total_rows={total_rows}", flush=True)
    with (ROOT / "data" / "angel_5m_download_report.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "token", "status", "rows", "chunks_ok", "chunks_failed", "error"])
        writer.writeheader()
        writer.writerows(report)
    summary = {"requested_symbols": len(watchlist), "ok": sum(x["status"] == "OK" for x in report), "partial": sum(x["status"] == "PARTIAL" for x in report), "failed": sum(x["status"] == "FAILED" for x in report), "no_token": sum(x["status"] == "NO_TOKEN" for x in report), "candles": total_rows, "days_requested": DAYS, "interval": "FIVE_MINUTE", "start_ist": start.isoformat(), "end_ist": end.isoformat()}
    (ROOT / "data" / "angel_5m_download_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("FINAL SUMMARY " + json.dumps(summary), flush=True)
    if total_rows == 0:
        raise SystemExit("No candles downloaded; see workflow logs and report.")
if __name__ == "__main__":
    main()
