#!/usr/bin/env python3
"""Test add/remove ideas on the same 180-day candles.
Each variant is reported separately for TRAIN (till 31 Aug) and TEST (Sep-Oct),
so an idea only counts if it also works on the months it was not tuned on."""
import glob
from pathlib import Path
import pandas as pd
import backtest_v1_smc_5m as bt

ROOT = Path(__file__).resolve().parent
SPLIT = "2026-09-01"
files = sorted(glob.glob(str(ROOT / "data" / "angel_5m" / "*_5m.csv")))

VARIANTS = [
    # name, sides, entry start (min), tp %, sl %, eod exit (min)
    ("1 Current (as live)",            "BOTH",  570, 2.0, 1.0, 915),
    ("2 SHORT only",                   "SHORT", 570, 2.0, 1.0, 915),
    ("3 Entry from 10:00",             "BOTH",  600, 2.0, 1.0, 915),
    ("4 SHORT only + 10:00",           "SHORT", 600, 2.0, 1.0, 915),
    ("5 TP 1.5%",                      "BOTH",  570, 1.5, 1.0, 915),
    ("6 TP 1%",                        "BOTH",  570, 1.0, 1.0, 915),
    ("7 SHORT + 10:00 + TP 1.5%",      "SHORT", 600, 1.5, 1.0, 915),
    ("8 SHORT + 10:00 + TP 1%",        "SHORT", 600, 1.0, 1.0, 915),
    ("9 SHORT + 10:00 + TP1.5 SL0.75", "SHORT", 600, 1.5, 0.75, 915),
    ("10 SHORT + 10:00 + exit 14:30",  "SHORT", 600, 2.0, 1.0, 870),
]

prepared = []
for f in files:
    df = bt.load_csv(f)
    if len(df) >= 100:
        prepared.append(bt.add_indicators(df))
print(f"symbols loaded: {len(prepared)}", flush=True)

sig_cache = {}
rows = []
for name, sides, start, tp, sl, eod in VARIANTS:
    bt.ENTRY_START_MIN, bt.TP_PERCENT, bt.STOP_LOSS_PERCENT, bt.EOD_EXIT_MIN = start, tp, sl, eod
    trades = []
    for k, d in enumerate(prepared):
        key = (k, start)
        if key not in sig_cache:
            sig_cache[key] = bt.find_signals(d)
        for s in sig_cache[key]:
            if sides == "SHORT" and s["direction"] != -1:
                continue
            trades.append(bt.simulate(d, s))
    t = pd.DataFrame(trades)
    for part, g in (("TRAIN Apr-Aug", t[t.entry_time < SPLIT]), ("TEST Sep-Oct", t[t.entry_time >= SPLIT]), ("FULL 180d", t)):
        s = bt.stats(g)
        rows.append({"variant": name, "period": part, **s})
    print(name, "done", flush=True)

out = pd.DataFrame(rows)
(ROOT / "data" / "backtest").mkdir(parents=True, exist_ok=True)
out.to_csv(ROOT / "data" / "backtest" / "variants.csv", index=False)
print(out[["variant", "period", "trades", "win_rate_pct", "total_net_rs", "profit_factor", "max_drawdown_rs"]].to_string())
