#!/usr/bin/env python3
"""Build index universe files (e.g. Nifty 500, Nifty Midcap 100).

  sector_pullback/<index>.txt          all index stocks (used for sector strength)
  sector_pullback/<index>_nonfno.txt   index stocks with NO stock futures (non-F&O) -> traded

  python build_universe.py --index nifty500     # or midcap100 / nifty100

F&O list comes from Angel One's scrip master (NFO FUTSTK underlyings), the
Nifty 100 list from niftyindices.com / NSE archives.
"""
from __future__ import annotations

import argparse
import io
import sys
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


def index_list(index: str) -> list[str]:
    for url in (b + INDEX_FILES[index] for b in BASES):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            syms = sorted(df["Symbol"].str.strip().str.upper().unique())
            print(f"{index} from {url}: {len(syms)} stocks")
            return syms
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {url}: {exc}")
    sys.exit(f"Could not download {index} list")


def fno_stocks() -> set[str]:
    master = requests.get(SCRIP_MASTER, timeout=120).json()
    fno = {str(r.get("name", "")).strip().upper() for r in master
           if r.get("exch_seg") == "NFO" and r.get("instrumenttype") == "FUTSTK"}
    print(f"F&O stocks (Angel NFO FUTSTK underlyings): {len(fno)}")
    return fno


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", choices=sorted(INDEX_FILES), default="midcap100")
    args = ap.parse_args()
    members = index_list(args.index)
    fno = fno_stocks()
    if len(fno) < 100:
        sys.exit("F&O list looks wrong (too few names) - aborting")
    non = [s for s in members if s not in fno]
    (HERE / f"{args.index}.txt").write_text("\n".join(members) + "\n")
    (HERE / f"{args.index}_nonfno.txt").write_text("\n".join(non) + "\n")
    print(f"Non-F&O stocks in {args.index} ({len(non)}): {', '.join(non)}")


if __name__ == "__main__":
    main()
