#!/usr/bin/env python3
"""NIFTY trend filter test. LONG only when Nifty trend is UP, SHORT only when DOWN.
Checked on the stock's signal candle using the Nifty candle of the SAME 5m time
(daily filters use only yesterday's data) - no look-ahead.
Base A = live scanner exactly as it is today. Base B = best setup (SHORT, 10:00, SL 1.5, TP 2, OB only, RSI off).
Also: entry-candle volume filters and 24h (75-candle) volume POC.\nTRAIN = Apr-Aug, TEST = Sep-Oct."""
import glob
from pathlib import Path
import numpy as np
import pandas as pd
import backtest_v1_smc_5m as bt

ROOT = Path(__file__).resolve().parent
SPLIT = "2026-09-01"


def load_nifty():
    n = pd.read_csv(ROOT / "data" / "nifty" / "NIFTY_5m.csv")
    n["time"] = pd.to_datetime(n["time_ist"], utc=True).dt.tz_convert(bt.IST)
    n = n.sort_values("time").drop_duplicates("time").set_index("time")
    n["date"] = n.index.date
    n["ema20"] = n["close"].ewm(span=20, adjust=False).mean()
    n["ema50"] = n["close"].ewm(span=50, adjust=False).mean()
    n["day_open"] = n.groupby("date")["open"].transform("first")
    dd = pd.read_csv(ROOT / "data" / "nifty" / "NIFTY_1d.csv")
    dd["date"] = pd.to_datetime(dd["time_ist"], utc=True).dt.tz_convert(bt.IST).dt.date
    dd = dd.sort_values("date").drop_duplicates("date").set_index("date")
    dd["ema20"] = dd["close"].ewm(span=20, adjust=False).mean()
    prev_close = dd["close"].shift(1)
    prev_trend = (dd["close"] > dd["ema20"]).shift(1)          # yesterday's close vs yesterday's EMA20
    prev_trend_dn = (dd["close"] < dd["ema20"]).shift(1)
    n["prev_close"] = n["date"].map(prev_close)
    n["d_up"] = n["date"].map(prev_trend).fillna(False).astype(bool)
    n["d_dn"] = n["date"].map(prev_trend_dn).fillna(False).astype(bool)
    return n


def rolling_poc(d, window=75, bins=40):
    """Price with the most traded volume over the previous `window` candles (current candle excluded)."""
    tp = ((d["high"] + d["low"] + d["close"]) / 3).to_numpy()
    lo, hi, vol = d["low"].to_numpy(), d["high"].to_numpy(), d["volume"].to_numpy()
    poc = np.full(len(d), np.nan)
    for i in range(window, len(d)):
        a, b = lo[i - window:i].min(), hi[i - window:i].max()
        if b <= a:
            continue
        h, edges = np.histogram(tp[i - window:i], bins=bins, range=(a, b), weights=vol[i - window:i])
        k = h.argmax()
        poc[i] = (edges[k] + edges[k + 1]) / 2
    return poc


N = load_nifty()


def nifty(up, dn):
    return lambda d: (up.reindex(d["time"]).fillna(False).astype(bool).to_numpy(),
                      dn.reindex(d["time"]).fillna(False).astype(bool).to_numpy())


def both(cond):
    return lambda d: (cond(d), cond(d))


MODES = {
    "N0 Koi extra filter nahi": None,
    "N1 Nifty vs 5m EMA20": nifty(N.close > N.ema20, N.close < N.ema20),
    "N2 Nifty vs 5m EMA50": nifty(N.close > N.ema50, N.close < N.ema50),
    "N3 Nifty vs aaj ka open": nifty(N.close > N.day_open, N.close < N.day_open),
    "N4 Nifty vs kal ka close": nifty(N.close > N.prev_close, N.close < N.prev_close),
    "N5 Nifty daily trend (EMA20)": nifty(N.d_up, N.d_dn),
    "N6 Daily trend + aaj ka open": nifty(N.d_up & (N.close > N.day_open), N.d_dn & (N.close < N.day_open)),
    "V1 Entry volume >= 1.5x avg": both(lambda d: (d.volume >= 1.5 * d.volume_sma).to_numpy()),
    "V2 Entry volume >= 2x avg": both(lambda d: (d.volume >= 2.0 * d.volume_sma).to_numpy()),
    "V3 Entry volume = 20 candle high": both(lambda d: (d.volume >= d.volume.rolling(20).max()).to_numpy()),
    "P1 POC 24h (S below / L above)": lambda d: ((d.close > d.poc).to_numpy(), (d.close < d.poc).to_numpy()),
}

BASES = {
    "A LIVE (koi badlav nahi)": dict(ENTRY_START_MIN=570, TP_PERCENT=2.0, STOP_LOSS_PERCENT=1.0,
                                    USE_FVG=True, USE_RSI_FILTER=True, sides=("LONG", "SHORT", "BOTH")),
    "B BEST setup (SHORT)": dict(ENTRY_START_MIN=600, TP_PERCENT=2.0, STOP_LOSS_PERCENT=1.5,
                                 USE_FVG=False, USE_RSI_FILTER=False, sides=("SHORT",)),
}

prepared = []
for f in sorted(glob.glob(str(ROOT / "data" / "angel_5m" / "*_5m.csv"))):
    df = bt.load_csv(f)
    if len(df) >= 100:
        d = bt.add_indicators(df)
        d["poc"] = rolling_poc(d)
        prepared.append(d)
print(f"symbols: {len(prepared)} | nifty 5m candles: {len(N)}", flush=True)

rows = []
for bname, cfg in BASES.items():
    cfg = dict(cfg)
    sides = cfg.pop("sides")
    for k, v in cfg.items():
        setattr(bt, k, v)
    for mname, mode in MODES.items():
        bt.USE_NIFTY_FILTER = mode is not None
        trades = []
        for d in prepared:
            if mode is not None:
                d["nifty_up"], d["nifty_dn"] = mode(d)
            for s in bt.find_signals(d):
                trades.append(bt.simulate(d, s))
        t = pd.DataFrame(trades)
        t["month"] = t.entry_time.str[:7]
        for side in sides:
            g = t if side == "BOTH" else t[t.side == side]
            for part, x in (("TRAIN", g[g.entry_time < SPLIT]), ("TEST", g[g.entry_time >= SPLIT]), ("FULL", g)):
                rows.append({"base": bname, "nifty": mname, "side": side, "period": part, **bt.stats(x)})
            for mon, x in g.groupby("month"):
                rows.append({"base": bname, "nifty": mname, "side": side, "period": mon, **bt.stats(x)})
        print(bname, mname, "done", flush=True)

out = ROOT / "data" / "backtest"
out.mkdir(parents=True, exist_ok=True)
pd.DataFrame(rows).to_csv(out / "nifty_filter.csv", index=False)
