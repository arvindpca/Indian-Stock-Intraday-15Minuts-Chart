#!/usr/bin/env python3
"""Best setup from condition tests: SHORT only, entry 10:00-14:00, TP 1.5%, SL 1%,
zone = Order Block only (FVG off), RSI filter off. Writes best_trades.csv + best_monthly.csv."""
import glob
from pathlib import Path
import pandas as pd
import backtest_v1_smc_5m as bt

ROOT = Path(__file__).resolve().parent
bt.ENTRY_START_MIN = 600
bt.USE_FVG, bt.USE_RSI_FILTER = False, False
out = ROOT / "data" / "backtest"
out.mkdir(parents=True, exist_ok=True)
prepared = []
for f in sorted(glob.glob(str(ROOT / "data" / "angel_5m" / "*_5m.csv"))):
    df = bt.load_csv(f)
    if len(df) >= 100:
        prepared.append((Path(f).name.replace("_5m.csv", ""), bt.add_indicators(df)))
for tag, sl, tp in (("sl1_tp1.5", 1.0, 1.5), ("sl1.5_tp2", 1.5, 2.0)):
    bt.STOP_LOSS_PERCENT, bt.TP_PERCENT = sl, tp
    trades = []
    for sym, d in prepared:
        for s in bt.find_signals(d):
            if s["direction"] == -1:
                trades.append({"symbol": sym, **bt.simulate(d, s)})
    t = pd.DataFrame(trades).sort_values("entry_time")
    t.to_csv(out / f"best_trades_{tag}.csv", index=False)
    t["month"] = t.entry_time.str[:7]
    m = t.groupby("month").apply(lambda g: pd.Series(bt.stats(g))).reset_index()
    m.to_csv(out / f"best_monthly_{tag}.csv", index=False)
    print(tag, "\n", m.to_string())
