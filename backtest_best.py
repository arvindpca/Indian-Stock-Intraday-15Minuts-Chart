#!/usr/bin/env python3
"""Best setup from condition tests: SHORT only, entry 10:00-14:00, TP 1.5%, SL 1%,
zone = Order Block only (FVG off), RSI filter off. Writes best_trades.csv + best_monthly.csv."""
import glob
from pathlib import Path
import pandas as pd
import backtest_v1_smc_5m as bt

ROOT = Path(__file__).resolve().parent
bt.ENTRY_START_MIN, bt.TP_PERCENT, bt.STOP_LOSS_PERCENT = 600, 1.5, 1.0
bt.USE_FVG, bt.USE_RSI_FILTER = False, False
trades = []
for f in sorted(glob.glob(str(ROOT / "data" / "angel_5m" / "*_5m.csv"))):
    df = bt.load_csv(f)
    if len(df) < 100:
        continue
    d = bt.add_indicators(df)
    sym = Path(f).name.replace("_5m.csv", "")
    for s in bt.find_signals(d):
        if s["direction"] == -1:
            trades.append({"symbol": sym, **bt.simulate(d, s)})
t = pd.DataFrame(trades).sort_values("entry_time")
out = ROOT / "data" / "backtest"
out.mkdir(parents=True, exist_ok=True)
t.to_csv(out / "best_trades.csv", index=False)
t["month"] = t.entry_time.str[:7]
m = t.groupby("month").apply(lambda g: pd.Series(bt.stats(g))).reset_index()
m.to_csv(out / "best_monthly.csv", index=False)
print(m.to_string())
