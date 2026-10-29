#!/usr/bin/env python3
"""Intraday leverage of our stocks from NSE's daily VaR margin file.

NSE publishes C_VAR1_<DDMMYYYY>_<n>.DAT every day with each stock's applicable margin rate
(VaR margin + Extreme Loss Margin, %). Brokers' intraday (MIS) margin cannot be below this,
so max leverage = 100 / applicable margin, capped at 5x.
Output: sector_pullback/margin_<date>.csv and a summary.
"""
from __future__ import annotations

import datetime as dt
import io
import sys
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
     "Accept": "*/*", "Referer": "https://www.nseindia.com/"}
BASES = ["https://nsearchives.nseindia.com/archives/nsccl/var/", "https://archives.nseindia.com/archives/nsccl/var/"]


def fetch():
    s = requests.Session()
    s.headers.update(H)
    today = dt.date.today()
    for back in range(0, 10):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        for n in (6, 5, 4, 3, 2, 1):          # later files of the day are more final
            for base in BASES:
                url = f"{base}C_VAR1_{d:%d%m%Y}_{n}.DAT"
                try:
                    r = s.get(url, timeout=30)
                    if r.status_code == 200 and len(r.text) > 1000:
                        print("OK", url)
                        return d, r.text
                except Exception as exc:  # noqa: BLE001
                    print("fail", url, exc)
    sys.exit("Could not download NSE VaR margin file")


d, text = fetch()
rows = []
for line in text.splitlines():
    p = [x.strip() for x in line.split(",")]
    if len(p) < 10 or p[2] not in ("EQ", "BE", "BZ", "SM", "ST"):
        continue
    try:
        rows.append({"symbol": p[1].upper(), "series": p[2], "var_margin": float(p[6]), "elm": float(p[7]),
                     "applicable_margin_pct": float(p[-1])})
    except ValueError:
        continue
mg = pd.DataFrame(rows).sort_values("series").drop_duplicates("symbol")
mg["max_leverage"] = (100 / mg["applicable_margin_pct"]).clip(upper=5).round(2)

lst = pd.read_csv(HERE / "stock_list_nifty500.csv")
non = lst[~lst["fno"]].copy()
non = non.merge(mg, on="symbol", how="left")
non["bucket"] = pd.cut(non["max_leverage"], [0, 2, 3, 4, 4.99, 5.01], labels=["<2x", "2-3x", "3-4x", "4-5x", "5x"])
non.to_csv(HERE / f"margin_{d:%Y-%m-%d}.csv", index=False)


def show(df, title):
    print(f"\n## {title}: {len(df)} stocks (NSE margin file {d})")
    print(df["bucket"].value_counts().reindex(["5x", "4-5x", "3-4x", "2-3x", "<2x"]).fillna(0).astype(int).to_string())
    print("no margin data:", int(df["max_leverage"].isna().sum()))


show(non, "All 289 non-F&O in Nifty 500")
show(non[non["selected"]], "Liquid list (182, price/volume/turnover ok)")
five = non[non["bucket"] == "5x"].sort_values(["sector", "symbol"])
print("\n5x stocks by sector:")
print(five.groupby("sector")["symbol"].apply(lambda s: ", ".join(s)).to_string())
