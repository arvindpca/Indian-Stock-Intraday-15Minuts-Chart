#!/usr/bin/env python3
"""Build the tradable stock list: index members -> non-F&O -> no penny -> volume.

Output: sector_pullback/stock_list_<index>.csv with one row per index stock:
  symbol, company, sector, fno, last_close, median_vol_20d, median_turnover_cr_20d,
  ok_price, ok_volume, ok_turnover, selected

Daily candles (last ~30 days) come from Angel One SmartAPI.
Env: ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
INDEX_FILES = {"nifty100": "ind_nifty100list.csv", "midcap100": "ind_niftymidcap100list.csv",
               "nifty500": "ind_nifty500list.csv"}
BASES = ["https://www.niftyindices.com/IndexConstituent/",
         "https://archives.nseindia.com/content/indices/"]
SCRIP_MASTER = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def index_members(index: str) -> pd.DataFrame:
    for url in (b + INDEX_FILES[index] for b in BASES):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = [c.strip() for c in df.columns]
            df = df.rename(columns={"Symbol": "symbol", "Company Name": "company", "Industry": "sector"})
            df["symbol"] = df["symbol"].str.strip().str.upper()
            print(f"{index}: {len(df)} stocks from {url}")
            return df[["symbol", "company", "sector"]]
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {url}: {exc}")
    sys.exit(f"Could not download {index} list")


def daily_stats(api, token: str, days: int = 45) -> dict:
    end = datetime.now()
    params = {"exchange": "NSE", "symboltoken": token, "interval": "ONE_DAY",
              "fromdate": (end - timedelta(days=days)).strftime("%Y-%m-%d 09:15"),
              "todate": end.strftime("%Y-%m-%d 15:30")}
    for attempt in range(3):
        try:
            resp = api.getCandleData(params)
            data = (resp or {}).get("data") or []
            if data:
                d = pd.DataFrame(data, columns=["ts", "o", "h", "l", "c", "v"]).tail(20)
                return {"last_close": float(d["c"].iloc[-1]),
                        "median_vol_20d": float(d["v"].median()),
                        "median_turnover_cr_20d": float((d["c"] * d["v"]).median() / 1e7)}
            return {}
        except Exception as exc:  # noqa: BLE001
            print(f"  retry {attempt + 1}: {exc}")
            time.sleep(2)
    return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", choices=sorted(INDEX_FILES), default="nifty500")
    ap.add_argument("--min-price", type=float, default=50.0)
    ap.add_argument("--min-volume", type=float, default=200_000)
    ap.add_argument("--min-turnover-cr", type=float, default=5.0)
    args = ap.parse_args()

    members = index_members(args.index)
    master = requests.get(SCRIP_MASTER, timeout=120).json()
    fno = {str(r.get("name", "")).strip().upper() for r in master
           if r.get("exch_seg") == "NFO" and r.get("instrumenttype") == "FUTSTK"}
    if len(fno) < 100:
        sys.exit("F&O list looks wrong - aborting")
    tokens = {str(r["symbol"]).upper()[:-3]: str(r["token"]) for r in master
              if r.get("exch_seg") == "NSE" and str(r.get("symbol", "")).upper().endswith("-EQ")}
    members["fno"] = members["symbol"].isin(fno)
    print(f"F&O underlyings: {len(fno)} | in {args.index}: {members['fno'].sum()} | "
          f"non-F&O: {(~members['fno']).sum()}")

    import pyotp
    from SmartApi import SmartConnect
    api = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
    login = api.generateSession(os.environ["ANGEL_CLIENT_ID"], os.environ["ANGEL_PASSWORD"],
                                pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now())
    if not login or not login.get("status"):
        sys.exit(f"Angel login failed: {(login or {}).get('message')}")

    rows = []
    non = members[~members["fno"]]
    for n, sym in enumerate(non["symbol"], 1):
        tok = tokens.get(sym)
        st = daily_stats(api, tok) if tok else {}
        rows.append({"symbol": sym, **st})
        time.sleep(0.4)
        if n % 50 == 0:
            print(f"  {n}/{len(non)} done")
    stats = pd.DataFrame(rows)
    out = members.merge(stats, on="symbol", how="left")
    out["ok_price"] = out["last_close"] >= args.min_price
    out["ok_volume"] = out["median_vol_20d"] >= args.min_volume
    out["ok_turnover"] = out["median_turnover_cr_20d"] >= args.min_turnover_cr
    out["selected"] = ~out["fno"] & out["ok_price"] & out["ok_volume"] & out["ok_turnover"]
    out = out.sort_values(["selected", "sector", "median_turnover_cr_20d"], ascending=[False, True, False])
    path = HERE / f"stock_list_{args.index}.csv"
    out.round(2).to_csv(path, index=False)

    nf = out[~out["fno"]]
    print(f"\n{args.index}: {len(out)} | F&O removed: {out['fno'].sum()} | non-F&O: {len(nf)}")
    print(f"  no data: {nf['last_close'].isna().sum()}")
    print(f"  penny (< Rs {args.min_price:g}): {(~nf['ok_price'] & nf['last_close'].notna()).sum()}")
    print(f"  low volume (< {args.min_volume:,.0f}): {(~nf['ok_volume'] & nf['last_close'].notna()).sum()}")
    print(f"  low turnover (< Rs {args.min_turnover_cr:g} Cr): "
          f"{(~nf['ok_turnover'] & nf['last_close'].notna()).sum()}")
    print(f"  FINAL SELECTED: {out['selected'].sum()}  -> {path}")
    print(out[out["selected"]].groupby("sector").size().sort_values(ascending=False).to_string())


if __name__ == "__main__":
    main()
