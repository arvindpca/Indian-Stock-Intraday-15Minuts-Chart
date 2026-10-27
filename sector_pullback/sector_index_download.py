#!/usr/bin/env python3
"""Download 2 years of 5-min candles for NSE sector indices from Angel One and map
NSE 'Industry' sectors to them.

Writes:
  research_data/raw/5m/IDX_<NAME>.csv.gz        one file per sector index
  sector_pullback/sector_index_map.csv          sector -> IDX_<NAME> (only sectors that have an index)
  sector_pullback/angel_index_list.csv          every NSE index name/token in Angel's scrip master

Credentials only from env (cleaned of invisible characters); SmartApi logging is silenced
because it prints request headers/body (credentials) on errors.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research.data_download import SCRIP_MASTER, _env, fetch_symbol  # noqa: E402

HERE = Path(__file__).resolve().parent
RAW = HERE.parent / "research_data" / "raw" / "5m"

# NSE Industry -> candidate Angel index names (normalised: lowercase, letters/digits only).
# First candidate found in Angel's scrip master wins.
SECTOR_INDEX = {
    "Financial Services": ["niftyfinservice", "niftyfinancialservices", "niftyfinservices", "finnifty"],
    "Information Technology": ["niftyit"],
    "Healthcare": ["niftyhealthcare", "niftyhealthcareindex", "niftypharma"],
    "Automobile and Auto Components": ["niftyauto"],
    "Metals & Mining": ["niftymetal"],
    "Fast Moving Consumer Goods": ["niftyfmcg"],
    "Realty": ["niftyrealty"],
    "Oil Gas & Consumable Fuels": ["niftyoilandgas", "niftyoilgas", "niftyenergy"],
    "Power": ["niftypower", "niftyenergy"],
    "Media Entertainment & Publication": ["niftymedia"],
    "Consumer Durables": ["niftyconsrdurbl", "niftyconsumerdurables", "niftyconsumerdurable"],
    "Chemicals": ["niftychemicals", "niftychemical"],
    "Capital Goods": ["niftycapitalgoods", "niftyindiamfg", "niftyindiamanufacturing"],
    "Construction": ["niftyinfra", "niftyinfrastructure"],
    "Construction Materials": ["niftycommodities"],
    "Services": ["niftyservsector", "niftyservicessector"],
    "Consumer Services": ["niftyconsumption", "niftyindiaconsumption"],
}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def main() -> None:
    master = requests.get(SCRIP_MASTER, timeout=120).json()
    idx = [r for r in master if str(r.get("exch_seg")) == "NSE"
           and str(r.get("instrumenttype", "")).upper() == "AMXIDX"]
    lst = pd.DataFrame([{"name": r.get("name"), "symbol": r.get("symbol"), "token": r.get("token")} for r in idx])
    lst.sort_values("name").to_csv(HERE / "angel_index_list.csv", index=False)
    print(f"Angel NSE indices: {len(lst)}")
    by_norm = {}
    for r in idx:
        for k in (r.get("name"), r.get("symbol")):
            if k:
                by_norm.setdefault(norm(str(k)), (str(r.get("name")), str(r["token"])))

    mapping, wanted = [], {}
    for sector, cands in SECTOR_INDEX.items():
        hit = next((by_norm[c] for c in cands if c in by_norm), None)
        if hit:
            fname = "IDX_" + re.sub(r"[^A-Z0-9]+", "_", hit[0].upper()).strip("_")
            mapping.append({"sector": sector, "index_name": hit[0], "index_file": fname})
            wanted[fname] = hit[1]
            print(f"  {sector:35s} -> {hit[0]}")
        else:
            print(f"  {sector:35s} -> (no index found)")
    pd.DataFrame(mapping).to_csv(HERE / "sector_index_map.csv", index=False)

    # silence SmartApi (it logs credentials on errors)
    import logzero
    logzero.loglevel(logging.CRITICAL + 1)
    import pyotp
    from SmartApi import SmartConnect
    api = SmartConnect(api_key=_env("ANGEL_API_KEY"))
    try:
        login = api.generateSession(_env("ANGEL_CLIENT_ID"), _env("ANGEL_PASSWORD"),
                                    pyotp.TOTP(_env("ANGEL_TOTP_SECRET")).now())
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Angel login failed ({type(exc).__name__})")
    if not login or not login.get("status"):
        sys.exit(f"Angel login failed: {(login or {}).get('message')}")

    end = datetime.now()
    start = end - timedelta(days=740)
    RAW.mkdir(parents=True, exist_ok=True)
    for fname, tok in wanted.items():
        p = RAW / f"{fname}.csv.gz"
        if p.exists():
            old = pd.read_csv(p, usecols=["datetime"], parse_dates=["datetime"])
            if len(old) and (end - old["datetime"].max()).days <= 4:
                print(f"  cached {fname}")
                continue
        df, errs = fetch_symbol(api, tok, start, end, 30, 0.4)
        if not df.empty:
            df.to_csv(p, index=False, compression="gzip")
        print(f"  {fname}: {len(df)} rows, {len(errs)} errors")


if __name__ == "__main__":
    main()
