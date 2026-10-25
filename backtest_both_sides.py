#!/usr/bin/env python3
"""LONG + SHORT both kept. Stack the improvements one by one and see the combined result.
TRAIN = Apr-Aug, TEST = Sep-Oct. Also writes month-wise for every step."""
import glob
from pathlib import Path
import numpy as np
import pandas as pd
import backtest_v1_smc_5m as bt

ROOT = Path(__file__).resolve().parent
SPLIT = "2026-09-01"


def rolling_poc(d, window=75, bins=40):
    tp = ((d["high"] + d["low"] + d["close"]) / 3).to_numpy()
    lo, hi, vol = d["low"].to_numpy(), d["high"].to_numpy(), d["volume"].to_numpy()
    poc = np.full(len(d), np.nan)
    for i in range(window, len(d)):
        a, b = lo[i - window:i].min(), hi[i - window:i].max()
        if b <= a:
            continue
        h, e = np.histogram(tp[i - window:i], bins=bins, range=(a, b), weights=vol[i - window:i])
        k = h.argmax()
        poc[i] = (e[k] + e[k + 1]) / 2
    return poc


def nifty_daily_trend():
    dd = pd.read_csv(ROOT / "data" / "nifty" / "NIFTY_1d.csv")
    dd["date"] = pd.to_datetime(dd["time_ist"], utc=True).dt.tz_convert(bt.IST).dt.date
    dd = dd.sort_values("date").drop_duplicates("date").set_index("date")
    ema = dd["close"].ewm(span=20, adjust=False).mean()
    return (dd["close"] > ema).shift(1), (dd["close"] < ema).shift(1)   # yesterday's state


UP, DN = nifty_daily_trend()

STEPS = [
    ("S0 LIVE abhi jaisa", dict(ENTRY_START_MIN=570, STOP_LOSS_PERCENT=1.0, TP_PERCENT=2.0, USE_FVG=True, USE_RSI_FILTER=True), None),
    ("S1 + entry 10:00 se", dict(ENTRY_START_MIN=600, STOP_LOSS_PERCENT=1.0, TP_PERCENT=2.0, USE_FVG=True, USE_RSI_FILTER=True), None),
    ("S2 + SL 1.5%", dict(ENTRY_START_MIN=600, STOP_LOSS_PERCENT=1.5, TP_PERCENT=2.0, USE_FVG=True, USE_RSI_FILTER=True), None),
    ("S3 + FVG band (sirf OB)", dict(ENTRY_START_MIN=600, STOP_LOSS_PERCENT=1.5, TP_PERCENT=2.0, USE_FVG=False, USE_RSI_FILTER=True), None),
    ("S4 + RSI band", dict(ENTRY_START_MIN=600, STOP_LOSS_PERCENT=1.5, TP_PERCENT=2.0, USE_FVG=False, USE_RSI_FILTER=False), None),
    ("S5 + Nifty daily trend", dict(ENTRY_START_MIN=600, STOP_LOSS_PERCENT=1.5, TP_PERCENT=2.0, USE_FVG=False, USE_RSI_FILTER=False), "nifty"),
    ("S6 + POC 24h", dict(ENTRY_START_MIN=600, STOP_LOSS_PERCENT=1.5, TP_PERCENT=2.0, USE_FVG=False, USE_RSI_FILTER=False), "poc"),
    ("S7 + Nifty daily + POC", dict(ENTRY_START_MIN=600, STOP_LOSS_PERCENT=1.5, TP_PERCENT=2.0, USE_FVG=False, USE_RSI_FILTER=False), "nifty+poc"),
    ("S8 S4 + Nifty sirf LONG pe", dict(ENTRY_START_MIN=600, STOP_LOSS_PERCENT=1.5, TP_PERCENT=2.0, USE_FVG=False, USE_RSI_FILTER=False), "nifty_long"),
]

prepared = []
for f in sorted(glob.glob(str(ROOT / "data" / "angel_5m" / "*_5m.csv"))):
    df = bt.load_csv(f)
    if len(df) < 100:
        continue
    d = bt.add_indicators(df)
    d["poc"] = rolling_poc(d)
    up = d["date"].map(UP).fillna(False).astype(bool).to_numpy()
    dn = d["date"].map(DN).fillna(False).astype(bool).to_numpy()
    c, poc = d["close"].to_numpy(), d["poc"].to_numpy()
    d["f_nifty"] = list(zip(up, dn))
    d["f_poc"] = list(zip(c > poc, c < poc))
    prepared.append(d)
print(f"symbols: {len(prepared)}", flush=True)

rows = []
for name, cfg, extra in STEPS:
    for k, v in cfg.items():
        setattr(bt, k, v)
    bt.USE_NIFTY_FILTER = extra is not None
    trades = []
    for d in prepared:
        if extra:
            nu = np.array([x[0] for x in d["f_nifty"]]); nd = np.array([x[1] for x in d["f_nifty"]])
            pu = np.array([x[0] for x in d["f_poc"]]); pd_ = np.array([x[1] for x in d["f_poc"]])
            if extra == "nifty":
                d["nifty_up"], d["nifty_dn"] = nu, nd
            elif extra == "poc":
                d["nifty_up"], d["nifty_dn"] = pu, pd_
            elif extra == "nifty+poc":
                d["nifty_up"], d["nifty_dn"] = nu & pu, nd & pd_
            elif extra == "nifty_long":
                d["nifty_up"], d["nifty_dn"] = nu, np.ones(len(d), bool)
        for s in bt.find_signals(d):
            trades.append(bt.simulate(d, s))
    t = pd.DataFrame(trades)
    t["month"] = t.entry_time.str[:7]
    for side in ("BOTH", "LONG", "SHORT"):
        g = t if side == "BOTH" else t[t.side == side]
        for part, x in (("TRAIN", g[g.entry_time < SPLIT]), ("TEST", g[g.entry_time >= SPLIT]), ("FULL", g)):
            rows.append({"step": name, "side": side, "period": part, **bt.stats(x)})
        for mon, x in g.groupby("month"):
            rows.append({"step": name, "side": side, "period": mon, **bt.stats(x)})
    print(name, "done", flush=True)

out = ROOT / "data" / "backtest"
out.mkdir(parents=True, exist_ok=True)
pd.DataFrame(rows).to_csv(out / "both_sides.csv", index=False)
