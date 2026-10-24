#!/usr/bin/env python3
"""Condition-by-condition test: switch ONE condition off/on (or tighten it) at a time
and compare with the base, separately for SHORT and LONG.
Base = entries from 10:00, TP 1.5%, SL 1%. TRAIN = Apr-Aug, TEST = Sep-Oct."""
import glob
from pathlib import Path
import pandas as pd
import backtest_v1_smc_5m as bt

ROOT = Path(__file__).resolve().parent
SPLIT = "2026-09-01"
BASE = dict(ENTRY_START_MIN=600, TP_PERCENT=1.5, STOP_LOSS_PERCENT=1.0)
DEFAULTS = {k: getattr(bt, k) for k in (
    "USE_VOLUME_FILTER", "VOLUME_MULT", "USE_RSI_FILTER", "RSI_LONG_MIN", "RSI_SHORT_MAX",
    "USE_VWAP_FILTER", "USE_FVG", "USE_OB", "USE_DISPLACEMENT", "MINIMUM_DISPLACEMENT_ATR",
    "USE_RETEST", "USE_CONFIRMATION", "USE_EMA_TREND", "SETUP_EXPIRY_BARS")}

TESTS_SINGLE = [
    ("00 BASE (sab conditions ON)", {}),
    ("01 Volume filter OFF", {"USE_VOLUME_FILTER": False}),
    ("02 Volume >= 1.5x avg", {"VOLUME_MULT": 1.5}),
    ("03 Volume >= 2x avg", {"VOLUME_MULT": 2.0}),
    ("04 RSI filter OFF", {"USE_RSI_FILTER": False}),
    ("05 RSI strict (S<40 / L>60)", {"RSI_SHORT_MAX": 40.0, "RSI_LONG_MIN": 60.0}),
    ("06 VWAP filter OFF", {"USE_VWAP_FILTER": False}),
    ("07 FVG zone OFF (only OB)", {"USE_FVG": False}),
    ("08 OB zone OFF (only FVG)", {"USE_OB": False}),
    ("09 Displacement OFF", {"USE_DISPLACEMENT": False}),
    ("10 Displacement >= 1.5 ATR", {"MINIMUM_DISPLACEMENT_ATR": 1.5}),
    ("11 Retest OFF", {"USE_RETEST": False}),
    ("12 Confirmation OFF", {"USE_CONFIRMATION": False}),
    ("13 EMA50 trend filter ON", {"USE_EMA_TREND": True}),
    ("14 Setup expiry 6 bars", {"SETUP_EXPIRY_BARS": 6}),
    ("15 Setup expiry 24 bars", {"SETUP_EXPIRY_BARS": 24}),
]

TESTS = TESTS_SINGLE + [
    ("C1 FVG OFF + RSI OFF", {"USE_FVG": False, "USE_RSI_FILTER": False}),
    ("C2 FVG OFF + expiry 24", {"USE_FVG": False, "SETUP_EXPIRY_BARS": 24}),
    ("C3 FVG OFF + RSI OFF + expiry 24", {"USE_FVG": False, "USE_RSI_FILTER": False, "SETUP_EXPIRY_BARS": 24}),
    ("C4 RSI OFF + expiry 24", {"USE_RSI_FILTER": False, "SETUP_EXPIRY_BARS": 24}),
]

files = sorted(glob.glob(str(ROOT / "data" / "angel_5m" / "*_5m.csv")))
prepared = [bt.add_indicators(df) for df in (bt.load_csv(f) for f in files) if len(df) >= 100]
print(f"symbols: {len(prepared)}", flush=True)

for k, v in BASE.items():
    setattr(bt, k, v)
rows = []
for name, change in TESTS:
    for k, v in DEFAULTS.items():
        setattr(bt, k, v)
    for k, v in change.items():
        setattr(bt, k, v)
    trades = []
    for d in prepared:
        for s in bt.find_signals(d):
            trades.append(bt.simulate(d, s))
    t = pd.DataFrame(trades)
    for side in ("SHORT", "LONG"):
        g = t[t.side == side] if len(t) else t
        for part, x in (("TRAIN", g[g.entry_time < SPLIT]), ("TEST", g[g.entry_time >= SPLIT]), ("FULL", g)):
            rows.append({"test": name, "side": side, "period": part, **bt.stats(x)})
    print(name, "done", flush=True)

out = pd.DataFrame(rows)
(ROOT / "data" / "backtest").mkdir(parents=True, exist_ok=True)
out.to_csv(ROOT / "data" / "backtest" / "conditions.csv", index=False)
