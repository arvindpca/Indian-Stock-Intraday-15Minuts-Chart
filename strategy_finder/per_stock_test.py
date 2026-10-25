#!/usr/bin/env python3
"""Does a DIFFERENT strategy per stock work?  Honest test:
For every stock, pick its best (event, side, exit) combo using ONLY Apr-Aug trades,
then trade exactly that combo on that stock in Sep-Oct.
Compared with: ONE combo for all stocks, also picked on Apr-Aug only."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import finder2 as f2

OUT = f2.OUT
t = pd.read_csv(OUT / "events.csv.gz")
exits = f2.ALL_EXITS
long_df = []
for side in ("long", "short"):
    for k in exits:
        x = t[["symbol", "date", "event", "period", f"{side}_{k}"]].rename(columns={f"{side}_{k}": "pnl"})
        x["combo"] = x["event"] + "|" + side + "|" + k
        long_df.append(x)
L = pd.concat(long_df, ignore_index=True).dropna(subset=["pnl"])
tr, te = L[L.period == "TRAIN"], L[L.period == "TEST"]
res = {}

# A) ONE combo for all stocks (best on TRAIN, min 150 trades)
g = tr.groupby("combo")["pnl"].agg(["size", "mean"])
best = g[g["size"] >= 150].sort_values("mean", ascending=False)
top = best.index[0]
x = te[te.combo == top]["pnl"]
res["one_for_all"] = {"combo": top, "train_trades": int(best.loc[top, "size"]), "train_avg": round(best.loc[top, "mean"], 3),
                      "test_trades": len(x), "test_avg": round(x.mean(), 3), "test_rs": round(x.sum() / 100 * 1e5)}

# B) best combo PER STOCK on TRAIN, then that combo on that stock in TEST
for min_n in (5, 8, 12):
    gs = tr.groupby(["symbol", "combo"])["pnl"].agg(["size", "mean", "sum"]).reset_index()
    gs = gs[gs["size"] >= min_n]
    pick = gs.sort_values("mean", ascending=False).drop_duplicates("symbol")
    pick = pick[pick["mean"] > 0]
    m = te.merge(pick[["symbol", "combo"]], on=["symbol", "combo"])
    res[f"per_stock_min{min_n}"] = {"stocks": len(pick), "train_avg_of_picks": round(pick["mean"].mean(), 3),
                                    "train_rs_of_picks": round(pick["sum"].sum() / 100 * 1e5),
                                    "test_trades": len(m), "test_avg": round(m["pnl"].mean(), 3),
                                    "test_rs": round(m["pnl"].sum() / 100 * 1e5),
                                    "test_stocks_profitable": int((m.groupby("symbol")["pnl"].sum() > 0).sum())}
(OUT / "per_stock_test.json").write_text(json.dumps(res, indent=2))
print(json.dumps(res, indent=2))
