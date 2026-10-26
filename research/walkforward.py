"""Rolling walk-forward on DEV+VAL (OOS untouched).

Pool = full stage-1 grid (every trigger x exit), which is defined BEFORE looking at any
data, so the pool itself carries no selection bias. In each fold the best config per side
is chosen on the train window (expectancy_R, min trades) and traded blind on the next window.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.engine import run_strategy
from research.metrics import summarize


def month_folds(dates: pd.Series, train_m: int, test_m: int):
    months = pd.PeriodIndex(sorted(pd.to_datetime(dates).dt.to_period("M").unique()))
    folds, i = [], 0
    while i + train_m + test_m <= len(months):
        folds.append((months[i:i + train_m], months[i + train_m:i + train_m + test_m]))
        i += test_m
    return folds


def walk_forward(P, cache, cfg, triggers, period_mask, log):
    wf = cfg["walkforward"]
    min_tr = max(30, cfg["discovery"]["min_trades_dev"] // 2)
    pool = {}
    for side in "LS":
        for trig in triggers:
            for sl in cfg["exits"]["sl_atr"]:
                for rr in cfg["exits"]["rr"]:
                    t = run_strategy(P, cache, cfg, trig, [], side, sl, rr, row_mask=period_mask)
                    if len(t):
                        t["month"] = pd.to_datetime(t["date"]).dt.to_period("M")
                    pool[(side, trig, sl, rr)] = t
    all_dates = pd.Series(P.dates[period_mask])
    folds = month_folds(all_dates, wf["train_months"], wf["test_months"])
    log.info("walk-forward: %d folds, pool=%d configs", len(folds), len(pool))
    fold_rows, test_trades = [], []
    for fi, (tr_m, te_m) in enumerate(folds):
        for side in "LS":
            best, best_val = None, -np.inf
            for key, t in pool.items():
                if key[0] != side or t.empty:
                    continue
                tr = t[t["month"].isin(tr_m)]
                if len(tr) >= min_tr and tr["R"].mean() > best_val:
                    best, best_val = key, tr["R"].mean()
            if best is None or best_val <= 0:          # nothing profitable in-sample -> stay flat
                fold_rows.append({"fold": fi, "side": side, "train": f"{tr_m[0]}..{tr_m[-1]}",
                                  "test": f"{te_m[0]}..{te_m[-1]}", "chosen": "FLAT (no positive config in train)",
                                  "train_expR": best_val if best is not None else None, "test_trades": 0})
                continue
            te = pool[best][pool[best]["month"].isin(te_m)].copy()
            te["fold"], te["config"] = fi, "|".join(map(str, best))
            test_trades.append(te)
            s = summarize(te, cfg["risk"]["capital"])
            fold_rows.append({"fold": fi, "side": side, "train": f"{tr_m[0]}..{tr_m[-1]}", "test": f"{te_m[0]}..{te_m[-1]}",
                              "chosen": "|".join(map(str, best)), "train_expR": best_val,
                              "test_trades": s.get("trades", 0), "test_expR": s.get("expectancy_R"),
                              "test_net": s.get("net_pnl"), "test_PF": s.get("profit_factor")})
    tt = pd.concat(test_trades, ignore_index=True) if test_trades else pd.DataFrame()
    summary = {}
    for side in "LS":
        sub = tt[tt["direction"] == ("LONG" if side == "L" else "SHORT")] if len(tt) else tt
        summary[side] = summarize(sub, cfg["risk"]["capital"]) if len(sub) else {"trades": 0}
    summary["combined"] = summarize(tt, cfg["risk"]["capital"]) if len(tt) else {"trades": 0}
    return pd.DataFrame(fold_rows), tt.drop(columns=["month"], errors="ignore"), summary
