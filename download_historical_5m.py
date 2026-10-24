#!/usr/bin/env python3
"""Download recent Yahoo Finance 5-minute candles for symbols in stock_names.txt.
Outputs data/5m/<SYMBOL>.csv, data/5m_all.csv and data/download_report.csv.
This is a data acquisition step, not a backtest result.
"""
import csv, json, os, random, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "5m"
OUT.mkdir(parents=True, exist_ok=True)
DAYS = max(1, min(int(os.getenv("BACKTEST_DAYS", "55")), 59))
MAX_SYMBOLS = max(0, int(os.getenv("MAX_SYMBOLS", "0")))  # 0 = all symbols
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"})
now = int(time.time())
start = now - DAYS * 86400

symbols = [x.strip().upper().replace(".NS", "") for x in (ROOT / "stock_names.txt").read_text(encoding="utf-8-sig").splitlines() if x.strip()]
symbols = list(dict.fromkeys(symbols))
if MAX_SYMBOLS:
    symbols = symbols[:MAX_SYMBOLS]
print(f"DATA DOWNLOAD START | symbols={len(symbols)} | interval=5m | days={DAYS}", flush=True)
all_rows, report = [], []
for idx, symbol in enumerate(symbols, 1):
    status, count, error = "FAILED", 0, ""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS"
        response = None
        for attempt in range(4):
            try:
                response = SESSION.get(url, params={"period1": start, "period2": now, "interval": "5m", "events": "history", "includePrePost": "false"}, timeout=25)
                if response.status_code == 429:
                    raise RuntimeError("Yahoo rate limit (429)")
                response.raise_for_status()
                payload = response.json()
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep((2 ** attempt) + random.random())
        results = (payload.get("chart") or {}).get("result") or []
        if not results:
            raise RuntimeError(str((payload.get("chart") or {}).get("error") or "No chart result"))
        item = results[0]
        stamps = item.get("timestamp") or []
        quote = ((item.get("indicators") or {}).get("quote") or [{}])[0]
        adj = ((item.get("indicators") or {}).get("adjclose") or [{}])
        rows = []
        for i, stamp in enumerate(stamps):
            vals = [quote.get(k, [None] * len(stamps))[i] for k in ("open", "high", "low", "close", "volume")]
            if any(v is None for v in vals):
                continue
            dt = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
            row = {"symbol": symbol, "time_utc": dt, "open": vals[0], "high": vals[1], "low": vals[2], "close": vals[3], "volume": vals[4]}
            rows.append(row)
        if not rows:
            raise RuntimeError("No valid OHLCV candles")
        path = OUT / f"{symbol}_5m.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
        all_rows.extend(rows)
        count, status = len(rows), "OK"
    except Exception as exc:
        error = str(exc)[:300]
    report.append({"symbol": symbol, "status": status, "rows": count, "error": error})
    if idx == 1 or idx % 10 == 0 or idx == len(symbols):
        print(f"PROGRESS {idx}/{len(symbols)} | ok={sum(r['status']=='OK' for r in report)} | failed={sum(r['status']!='OK' for r in report)}", flush=True)
    time.sleep(0.15)
if all_rows:
    with (ROOT / "data" / "5m_all.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0]))
        writer.writeheader(); writer.writerows(all_rows)
with (ROOT / "data" / "download_report.csv").open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["symbol", "status", "rows", "error"])
    writer.writeheader(); writer.writerows(report)
summary = {"requested": len(symbols), "success": sum(r["status"]=="OK" for r in report), "failed": sum(r["status"]!="OK" for r in report), "rows": len(all_rows), "days_requested": DAYS, "interval": "5m", "generated_utc": datetime.now(timezone.utc).isoformat()}
(ROOT / "data" / "download_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print("DOWNLOAD SUMMARY " + json.dumps(summary), flush=True)
if not all_rows:
    raise SystemExit("No candle data downloaded; see report.")
