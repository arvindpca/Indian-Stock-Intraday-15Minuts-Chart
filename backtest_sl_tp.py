#!/usr/bin/env python3
"""SL / TP / trailing-SL grid on the SAME entries (LONG + SHORT).
Entries: entry from 10:00, Order Block zone only (FVG off), RSI off, rest as live.
Exit rules per candle (conservative): first check SL at the stop set by earlier candles,
then TP, then move the trailing stop using this candle's high/low. EOD exit at 15:15 close.
TRAIN = Apr-Aug, TEST = Sep-Oct."""
import glob
import itertools
from pathlib import Path
import numpy as np
import pandas as pd
import backtest_v1_smc_5m as bt

ROOT = Path(__file__).resolve().parent
SPLIT = "2026-09-01"
COST = bt.COST_PCT
bt.ENTRY_START_MIN = 600
bt.USE_FVG = False
bt.USE_RSI_FILTER = False

SLS = [0.5, 0.75, 1.0, 1.5]
TPS = [1.0, 1.5, 2.0, 3.0, None]          # None = no target, exit by SL/trail/EOD
TRAILS = [                                  # (name, activate at +%, trail distance %; 0 = move to breakeven)
    ("No trail", None, None),
    ("BE @0.5%", 0.5, 0.0),
    ("BE @1%", 1.0, 0.0),
    ("Trail 0.5% after +0.5%", 0.5, 0.5),
    ("Trail 0.75% after +1%", 1.0, 0.75),
    ("Trail 1% after +1%", 1.0, 1.0),
]

# ---- collect entries once; keep each trade's remaining same-day candles as arrays ----
entries = []
for f in sorted(glob.glob(str(ROOT / "data" / "angel_5m" / "*_5m.csv"))):
    df = bt.load_csv(f)
    if len(df) < 100:
        continue
    d = bt.add_indicators(df)
    O, H, L, C = (d[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    M, D = d["minute"].to_numpy(), d["date"].to_numpy()
    for s in bt.find_signals(d):
        i = s["i"]
        j = i + 1
        while j < len(d) and D[j] == D[i] and M[j - 1] < bt.EOD_EXIT_MIN:
            j += 1
        # candles i+1 .. j-1 are tradable; last one is the EOD candle
        entries.append({
            "side": s["direction"], "entry": C[i], "time": d["time"].iat[i].strftime("%Y-%m-%d %H:%M"),
            "o": O[i + 1:j], "h": H[i + 1:j], "l": L[i + 1:j], "c": C[i + 1:j],
        })
print(f"entries: {len(entries)}", flush=True)


def run(e, sl_pct, tp_pct, act, dist):
    side, entry = e["side"], e["entry"]
    if len(e["c"]) == 0:
        return 0.0
    # work in "profit %" space so LONG and SHORT share code
    if side == 1:
        hi_p = (e["h"] / entry - 1) * 100       # best move inside candle
        lo_p = (e["l"] / entry - 1) * 100       # worst move inside candle
        op_p = (e["o"] / entry - 1) * 100
        cl_p = (e["c"] / entry - 1) * 100
    else:
        hi_p = (1 - e["l"] / entry) * 100
        lo_p = (1 - e["h"] / entry) * 100
        op_p = (1 - e["o"] / entry) * 100
        cl_p = (1 - e["c"] / entry) * 100
    stop = -sl_pct
    best = 0.0
    for k in range(len(cl_p)):
        if lo_p[k] <= stop:
            return min(stop, op_p[k])          # gap through stop fills at open
        if tp_pct is not None and hi_p[k] >= tp_pct:
            return max(tp_pct, op_p[k])
        best = max(best, hi_p[k])
        if act is not None and best >= act:
            stop = max(stop, best - dist)       # dist 0 => breakeven at entry
    return cl_p[-1]                             # EOD close


meta = pd.DataFrame([{"side": "LONG" if e["side"] == 1 else "SHORT", "time": e["time"]} for e in entries])
rows = []
for sl, tp, (tname, act, dist) in itertools.product(SLS, TPS, TRAILS):
    gross = np.array([run(e, sl, tp, act, dist) for e in entries])
    net = gross - COST
    for side in ("LONG", "SHORT", "BOTH"):
        mask_s = np.ones(len(meta), bool) if side == "BOTH" else (meta.side == side).to_numpy()
        for part in ("TRAIN", "TEST", "FULL"):
            if part == "TRAIN":
                m = mask_s & (meta.time < SPLIT).to_numpy()
            elif part == "TEST":
                m = mask_s & (meta.time >= SPLIT).to_numpy()
            else:
                m = mask_s
            x = net[m]
            if len(x) == 0:
                continue
            rs = x / 100 * bt.CAPITAL_PER_TRADE
            eq = np.cumsum(rs)
            losses = -x[x <= 0].sum()
            rows.append({
                "sl": sl, "tp": "none" if tp is None else tp, "trail": tname, "side": side, "period": part,
                "trades": len(x), "win_rate_pct": round((x > 0).mean() * 100, 1),
                "avg_net_pct": round(x.mean(), 3), "total_net_rs": round(rs.sum(), 0),
                "profit_factor": round(x[x > 0].sum() / losses, 2) if losses > 0 else None,
                "max_drawdown_rs": round(float((eq - np.maximum.accumulate(eq)).min()), 0),
            })
    print(f"SL {sl} TP {tp} {tname} done", flush=True)

out = ROOT / "data" / "backtest"
out.mkdir(parents=True, exist_ok=True)
pd.DataFrame(rows).to_csv(out / "sl_tp_grid.csv", index=False)
