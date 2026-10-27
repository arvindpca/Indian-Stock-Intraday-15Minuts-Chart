#!/usr/bin/env python3
"""Download NSE index constituent lists and write symbol -> sector (Industry) map.

Output: sector_pullback/sector_map.csv  (symbol,sector)
Run on a machine with internet (GitHub Actions runner is fine).
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd
import requests

URLS = [
    "https://www.niftyindices.com/IndexConstituent/ind_niftytotalmarket_list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_niftymicrocap250_list.csv",
]
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept": "text/csv,*/*"}
OUT = Path(__file__).with_name("sector_map.csv")


def main() -> None:
    frames = []
    for url in URLS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = [c.strip() for c in df.columns]
            frames.append(df[["Symbol", "Industry"]])
            print(f"OK   {url}  ({len(df)} rows)")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {url}: {exc}")
    if not frames:
        sys.exit("Could not download any NSE constituent list")
    m = (pd.concat(frames).dropna().drop_duplicates("Symbol")
         .rename(columns={"Symbol": "symbol", "Industry": "sector"}))
    m["symbol"] = m["symbol"].str.strip().str.upper()
    m["sector"] = m["sector"].str.strip()
    m.sort_values("symbol").to_csv(OUT, index=False)
    print(f"Wrote {OUT} with {len(m)} symbols, {m['sector'].nunique()} sectors")


if __name__ == "__main__":
    main()
