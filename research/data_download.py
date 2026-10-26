#!/usr/bin/env python3
"""Resumable Angel One SmartAPI 5-minute downloader for the research universe.

* Credentials only from env vars (GitHub Secrets). Never printed.
* Re-uses repo seed files (RELIANCE / NIFTY 3y) and already-cached symbols.
* Symbol mapping via Angel scrip master; missing symbols are REPORTED, never replaced.
* Writes research_data/raw/5m/<SYMBOL>.csv.gz + research_data/reports/download_report.csv
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import unicodedata
from datetime import datetime, timedelta

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from research.common import ROOT, get_logger, load_config, load_universe, raw_path, write_json  # noqa: E402

SCRIP_MASTER = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
INDEX_TOKENS = {"NIFTY": "99926000"}
log = get_logger("download")


def _env(name: str) -> str:
    return "".join(ch for ch in os.getenv(name, "").strip() if unicodedata.category(ch) != "Cf")


def seed_from_repo(cfg: dict) -> list[str]:
    """Copy validated repo data into the raw store (no download needed)."""
    seeded = []
    for sym, rel in (cfg["data"].get("local_seed") or {}).items():
        src = ROOT / rel
        dst = raw_path(cfg, sym)
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            df = pd.read_csv(src, parse_dates=["datetime"])
            df.to_csv(dst, index=False, compression="gzip")
            seeded.append(sym)
    return seeded


def _is_fresh(cfg: dict, sym: str, start: datetime) -> bool:
    p = raw_path(cfg, sym)
    if not p.exists():
        return False
    df = pd.read_csv(p, usecols=["datetime"], parse_dates=["datetime"])
    if df.empty:
        return False
    stale_days = cfg["data"]["refresh_if_older_than_days"]
    covers_start = df["datetime"].min() <= start + timedelta(days=5)
    recent = (datetime.now() - df["datetime"].max()).days <= stale_days + 3  # weekends/holidays
    return bool(covers_start and recent)


def fetch_symbol(api, token: str, start: datetime, end: datetime, chunk: int, gap: float):
    rows, errors, cur = {}, [], start
    while cur < end:
        nxt = min(cur + timedelta(days=chunk), end)
        params = {"exchange": "NSE", "symboltoken": token, "interval": "FIVE_MINUTE",
                  "fromdate": cur.strftime("%Y-%m-%d 09:15"), "todate": nxt.strftime("%Y-%m-%d 15:30")}
        for attempt in range(3):
            try:
                r = api.getCandleData(params)
                if not r or not r.get("status"):
                    raise RuntimeError(str((r or {}).get("message", "empty response"))[:160])
                for row in r.get("data") or []:
                    if len(row) >= 6:
                        rows[str(row[0])] = row[:6]
                break
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                wait = 30 if any(k in msg for k in ("rate", "too many", "access denied")) else 3
                if attempt == 2:
                    errors.append(f"{cur.date()}..{nxt.date()}: {str(exc)[:120]}")
                time.sleep(wait)
        cur = nxt + timedelta(days=1)
        time.sleep(gap)
    df = pd.DataFrame(list(rows.values()), columns=["datetime", "open", "high", "low", "close", "volume"])
    if not df.empty:
        # Angel returns +05:30 aware strings -> naive IST
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        df = df.sort_values("datetime").reset_index(drop=True)
    return df, errors


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-symbols", type=int, default=0)
    ap.add_argument("--time-budget-min", type=float, default=300, help="stop cleanly before CI timeout")
    args = ap.parse_args()

    cfg = load_config()
    t0 = time.time()
    end = datetime.now()
    start = end - timedelta(days=cfg["data"]["history_days"])
    seeded = seed_from_repo(cfg)
    if seeded:
        log.info("seeded from repo files: %s", seeded)

    symbols = load_universe(cfg)
    if args.max_symbols:
        symbols = symbols[: args.max_symbols]
    symbols = [cfg["market_symbol"]] + [s for s in symbols if s != cfg["market_symbol"]]
    todo = [s for s in symbols if not _is_fresh(cfg, s, start)]
    log.info("universe=%d | already cached & fresh=%d | to download=%d", len(symbols), len(symbols) - len(todo), len(todo))
    report_path = ROOT / cfg["data"]["report_dir"] / "download_report.csv"
    old = pd.read_csv(report_path) if report_path.exists() else pd.DataFrame()
    report = {r["symbol"]: r for r in old.to_dict("records")} if not old.empty else {}
    for s in symbols:
        if s not in todo:
            report.setdefault(s, {"symbol": s, "token": "", "status": "CACHED", "rows": None, "errors": ""})

    if todo:
        missing = [k for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET") if not _env(k)]
        if missing:
            log.error("Angel credentials missing in env: %s -> cannot download %d symbols", missing, len(todo))
            for s in todo:
                report[s] = {"symbol": s, "token": "", "status": "NOT_DOWNLOADED_NO_CREDENTIALS", "rows": 0, "errors": ""}
        else:
            from SmartApi import SmartConnect
            import pyotp

            master = requests.get(SCRIP_MASTER, timeout=60).json()
            tokens = {str(i["symbol"]).upper()[:-3]: str(i["token"]) for i in master
                      if str(i.get("exch_seg", "")).upper() == "NSE" and str(i.get("symbol", "")).upper().endswith("-EQ")}
            tokens.update(INDEX_TOKENS)
            api = SmartConnect(api_key=_env("ANGEL_API_KEY"))
            login = api.generateSession(_env("ANGEL_CLIENT_ID"), _env("ANGEL_PASSWORD"), pyotp.TOTP(_env("ANGEL_TOTP_SECRET")).now())
            if not login or not login.get("status"):
                raise SystemExit("Angel login failed: " + str((login or {}).get("message")))
            for n, sym in enumerate(todo, 1):
                if (time.time() - t0) / 60 > args.time_budget_min:
                    log.warning("time budget reached; %d symbols left for next (resumed) run", len(todo) - n + 1)
                    break
                tok = tokens.get(sym)
                if not tok:
                    report[sym] = {"symbol": sym, "token": "", "status": "NO_TOKEN", "rows": 0, "errors": "not in Angel NSE-EQ master (renamed/delisted?)"}
                    continue
                df, errs = fetch_symbol(api, tok, start, end, cfg["data"]["chunk_days"], cfg["data"]["request_gap_sec"])
                if not df.empty:
                    p = raw_path(cfg, sym)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    df.to_csv(p, index=False, compression="gzip")
                status = "OK" if (not df.empty and not errs) else ("PARTIAL" if not df.empty else "FAILED")
                report[sym] = {"symbol": sym, "token": tok, "status": status, "rows": len(df), "errors": " | ".join(errs)[:500]}
                if n % 10 == 0 or n == len(todo):
                    log.info("progress %d/%d (%.1f min)", n, len(todo), (time.time() - t0) / 60)
                    pd.DataFrame(report.values()).to_csv(report_path, index=False)  # resumable checkpoint

    rep = pd.DataFrame(report.values())
    report_path.parent.mkdir(parents=True, exist_ok=True)
    rep.to_csv(report_path, index=False)
    present = sum(raw_path(cfg, s).exists() for s in symbols)
    write_json(report_path.with_name("download_summary.json"), {
        "requested": len(symbols), "files_present": present, "status_counts": rep["status"].value_counts().to_dict(),
        "history_days": cfg["data"]["history_days"], "run_end": datetime.now().isoformat()})
    log.info("files present: %d/%d", present, len(symbols))


if __name__ == "__main__":
    main()
