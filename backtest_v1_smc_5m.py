#!/usr/bin/env python3
"""
180-day backtest of the live scanner
  "Indian_NSE_TradingView_V1_SMC_FINAL_CANDIDATE_5M_LIVE_VWAP_2.py"

Replays the SAME bar-by-bar V1 SMC state machine over the whole history:
  Sweep -> Displacement -> MSS -> FVG/OB -> Retest -> Confirmation
  + Volume >= SMA20, RSI14 >50/<50, session VWAP, LONG < 12:00 (no BULL FVG longs),
    SHORT <= 14:00, entries from 09:30 IST, one completed setup per stock per day.

Trade model (matches the live alert plan):
  entry = close of the signal candle, SL 1%, TP 2%.
  Exits checked on the following 5m candles' high/low.  If SL and TP are both
  inside one candle, SL is assumed first (conservative).  Anything still open is
  squared off at the close of the 15:15 IST candle (intraday).
  Net P&L deducts ROUND_TRIP_COST_PCT (default 0.10%) for brokerage/STT/charges.

Input: CSVs from download_angel_5m_6m.py (data/angel_5m/*_5m.csv, time_ist) or
       download_historical_5m.py (data/5m/*_5m.csv, time_utc).
Output: data/backtest/trades.csv, per_symbol.csv, monthly.csv, summary.json
Signal research only. Places no orders.
"""
import glob
import json
import math
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
IST = ZoneInfo("Asia/Kolkata")

# ---- strategy settings (identical to the live file) ----
STOP_LOSS_PERCENT = 1.0
TP_PERCENT = 2.0
SWING_LEFT = SWING_RIGHT = 2
ATR_LENGTH = 14
MINIMUM_DISPLACEMENT_ATR = 1.0
USE_FVG = True
USE_OB = True
OB_LOOKBACK = 5
USE_VOLUME_FILTER = True
VOLUME_LENGTH = 20
USE_VWAP_FILTER = True
USE_RSI_FILTER = True
RSI_LENGTH = 14
RSI_LONG_MIN = 50.0
RSI_SHORT_MAX = 50.0
SETUP_EXPIRY_BARS = 12
# extra switches for condition testing (defaults = live behaviour)
USE_RETEST = True
USE_CONFIRMATION = True
VOLUME_MULT = 1.0          # volume >= VOLUME_MULT x SMA20
USE_EMA_TREND = False      # SHORT only below EMA, LONG only above EMA
EMA_TREND_LEN = 50
USE_DISPLACEMENT = True
ENTRY_START_MIN = 9 * 60 + 30
LONG_END_MIN = 12 * 60          # LONG entry candle must start before 12:00
SHORT_END_MIN = 14 * 60         # SHORT entry candle start <= 14:00
EOD_EXIT_MIN = 15 * 60 + 15     # square off at close of 15:15 candle

DAYS = int(os.getenv("BACKTEST_DAYS", "180"))
COST_PCT = float(os.getenv("ROUND_TRIP_COST_PCT", "0.10"))
CAPITAL_PER_TRADE = float(os.getenv("CAPITAL_PER_TRADE", "100000"))


def load_csv(path):
    df = pd.read_csv(path)
    tcol = "time_ist" if "time_ist" in df.columns else ("time_utc" if "time_utc" in df.columns else "time")
    df["time"] = pd.to_datetime(df[tcol], utc=True, errors="coerce").dt.tz_convert(IST)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["time", "open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0.0)
    # keep regular session candles only (09:15 - 15:25 IST)
    m = df["time"].dt.hour * 60 + df["time"].dt.minute
    df = df[(m >= 9 * 60 + 15) & (m <= 15 * 60 + 25)]
    df = df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    if len(df):
        cutoff = df["time"].iloc[-1] - pd.Timedelta(days=DAYS)
        df = df[df["time"] >= cutoff].reset_index(drop=True)
    return df


def add_indicators(d):
    d = d.copy()
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1.0 / ATR_LENGTH, adjust=False, min_periods=ATR_LENGTH).mean()
    d["volume_sma"] = d["volume"].rolling(VOLUME_LENGTH).mean()
    d["ema_trend"] = d["close"].ewm(span=EMA_TREND_LEN, adjust=False).mean()
    day = d["time"].dt.date
    tp = (d["high"] + d["low"] + d["close"]) / 3.0
    den = d["volume"].groupby(day).cumsum()
    d["vwap"] = ((tp * d["volume"]).groupby(day).cumsum() / den.replace(0, np.nan)).fillna(tp)
    delta = d["close"].diff()
    ag = delta.clip(lower=0).ewm(alpha=1.0 / RSI_LENGTH, adjust=False, min_periods=RSI_LENGTH).mean()
    al = (-delta.clip(upper=0)).ewm(alpha=1.0 / RSI_LENGTH, adjust=False, min_periods=RSI_LENGTH).mean()
    d["rsi"] = (100 - 100 / (1 + ag / al.replace(0, np.nan))).fillna(100.0)
    # confirmed pivots: centre bar strictly above/below 2 left & 2 right, flagged on i+2
    h, l = d["high"], d["low"]
    lh = h.shift(1).rolling(SWING_LEFT).max()
    rh = h[::-1].shift(1).rolling(SWING_RIGHT).max()[::-1]
    ll = l.shift(1).rolling(SWING_LEFT).min()
    rl = l[::-1].shift(1).rolling(SWING_RIGHT).min()[::-1]
    ph = (h > lh) & (h > rh)
    pl = (l < ll) & (l < rl)
    d["pivot_high"] = ph.shift(SWING_RIGHT, fill_value=False).astype(bool)
    d["pivot_low"] = pl.shift(SWING_RIGHT, fill_value=False).astype(bool)
    d["minute"] = d["time"].dt.hour * 60 + d["time"].dt.minute
    d["date"] = day
    return d


def find_signals(d):
    """Bar-by-bar copy of scan_stock(); returns every entry, not just the last bar."""
    O, H, L, C, V = (d[c].to_numpy(float) for c in ("open", "high", "low", "close", "volume"))
    ATR, VSMA, VWAP, RSI, EMA = (d[c].to_numpy(float) for c in ("atr", "volume_sma", "vwap", "rsi", "ema_trend"))
    PH, PL = d["pivot_high"].to_numpy(), d["pivot_low"].to_numpy()
    MIN, DATE = d["minute"].to_numpy(), d["date"].to_numpy()

    out = []
    state = direction = 0
    start = None
    mss_high = mss_low = zone_high = zone_low = None
    zone_type = ""
    psh = psl = None
    stage = {}

    def reset():
        nonlocal state, direction, start, mss_high, mss_low, zone_high, zone_low, zone_type
        state = direction = 0
        start = None
        mss_high = mss_low = zone_high = zone_low = None
        zone_type = ""

    for i in range(len(d)):
        if PH[i]:
            psh = H[i - SWING_RIGHT]
        if PL[i]:
            psl = L[i - SWING_RIGHT]
        if i > 0 and DATE[i] != DATE[i - 1]:
            reset()
        atr = ATR[i]
        if math.isnan(atr):
            continue
        body = abs(C[i] - O[i])
        bull_disp = C[i] > O[i] and body >= atr * MINIMUM_DISPLACEMENT_ATR
        bear_disp = C[i] < O[i] and body >= atr * MINIMUM_DISPLACEMENT_ATR
        vol_pass = (not USE_VOLUME_FILTER) or (not math.isnan(VSMA[i]) and V[i] >= VSMA[i] * VOLUME_MULT)
        bull_sweep = psl is not None and L[i] < psl and C[i] > psl
        bear_sweep = psh is not None and H[i] > psh and C[i] < psh

        if state == 0:
            if bull_sweep:
                state, direction, start = 1, 1, i
                mss_high, mss_low, zone_high, zone_low, zone_type = psh, None, None, None, ""
                stage = {"sweep": i}
            elif bear_sweep:
                state, direction, start = 1, -1, i
                mss_low, mss_high, zone_high, zone_low, zone_type = psl, None, None, None, ""
                stage = {"sweep": i}

        if 0 < state < 7 and start is not None and i - start > SETUP_EXPIRY_BARS:
            reset()

        if state == 1 and (not USE_DISPLACEMENT or (direction == 1 and bull_disp) or (direction == -1 and bear_disp)):
            state = 2
            stage["disp"] = i
            stage["disp_atr"] = body / atr

        if state == 2:
            if (direction == 1 and mss_high is not None and C[i] > mss_high) or \
               (direction == -1 and mss_low is not None and C[i] < mss_low):
                state = 3

        bull_fvg = i >= 2 and L[i] > H[i - 2]
        bear_fvg = i >= 2 and H[i] < L[i - 2]
        if state == 3 and USE_FVG:
            if direction == 1 and bull_fvg:
                zone_low, zone_high, zone_type, state, start = H[i - 2], L[i], "BULL FVG", 4, i
            elif direction == -1 and bear_fvg:
                zone_low, zone_high, zone_type, state, start = H[i], L[i - 2], "BEAR FVG", 4, i
        if state == 3 and USE_OB:
            for lb in range(1, OB_LOOKBACK + 1):
                j = i - lb
                if j < 0:
                    break
                if (direction == 1 and C[j] < O[j]) or (direction == -1 and C[j] > O[j]):
                    zone_high, zone_low = H[j], L[j]
                    zone_type = "BULL OB" if direction == 1 else "BEAR OB"
                    state, start = 4, i
                    break

        touched = zone_high is not None and zone_low is not None and H[i] >= zone_low and L[i] <= zone_high
        if state == 4 and start is not None and i > start and (touched or not USE_RETEST):
            state = 5

        if state == 5 and i >= 1:
            if (not USE_CONFIRMATION) or (direction == 1 and C[i] > O[i] and C[i] > H[i - 1]) or \
               (direction == -1 and C[i] < O[i] and C[i] < L[i - 1]):
                state = 6

        rsi = RSI[i]
        rsi_pass = (not USE_RSI_FILTER) or (direction == 1 and rsi > RSI_LONG_MIN) or (direction == -1 and rsi < RSI_SHORT_MAX)
        vwap_pass = (not USE_VWAP_FILTER) or (direction == 1 and C[i] > VWAP[i]) or (direction == -1 and C[i] < VWAP[i])
        m = MIN[i]
        time_pass = m >= ENTRY_START_MIN and ((direction == 1 and m < LONG_END_MIN) or (direction == -1 and m <= SHORT_END_MIN))
        zone_pass = not (direction == 1 and zone_type == "BULL FVG")
        ema_pass = (not USE_EMA_TREND) or (direction == 1 and C[i] > EMA[i]) or (direction == -1 and C[i] < EMA[i])

        if state == 6 and time_pass and zone_pass and vol_pass and rsi_pass and vwap_pass and ema_pass:
            out.append({"i": i, "direction": direction, "zone_type": zone_type,
                        "disp_atr": stage.get("disp_atr", float("nan")), "rsi": rsi})
            state = 7
    return out


def simulate(d, sig):
    i, direction = sig["i"], sig["direction"]
    entry = float(d["close"].iat[i])
    sl = entry * (1 - STOP_LOSS_PERCENT / 100) if direction == 1 else entry * (1 + STOP_LOSS_PERCENT / 100)
    tp = entry * (1 + TP_PERCENT / 100) if direction == 1 else entry * (1 - TP_PERCENT / 100)
    day = d["date"].iat[i]
    exit_px, reason, j = None, None, i
    for j in range(i + 1, len(d)):
        if d["date"].iat[j] != day:
            j -= 1
            exit_px, reason = float(d["close"].iat[j]), "EOD"
            break
        hi, lo = d["high"].iat[j], d["low"].iat[j]
        if direction == 1:
            if lo <= sl:
                # gap below SL fills at the open
                exit_px, reason = min(sl, float(d["open"].iat[j])), "SL"
                break
            if hi >= tp:
                exit_px, reason = max(tp, float(d["open"].iat[j])), "TP"
                break
        else:
            if hi >= sl:
                exit_px, reason = max(sl, float(d["open"].iat[j])), "SL"
                break
            if lo <= tp:
                exit_px, reason = min(tp, float(d["open"].iat[j])), "TP"
                break
        if d["minute"].iat[j] >= EOD_EXIT_MIN:
            exit_px, reason = float(d["close"].iat[j]), "EOD"
            break
    if exit_px is None:  # data ended
        exit_px, reason = float(d["close"].iat[j]), "EOD"
    gross = (exit_px / entry - 1) * 100 * direction
    return {
        "entry_time": d["time"].iat[i].strftime("%Y-%m-%d %H:%M"),
        "exit_time": d["time"].iat[j].strftime("%Y-%m-%d %H:%M"),
        "side": "LONG" if direction == 1 else "SHORT",
        "zone": sig["zone_type"],
        "entry": round(entry, 2), "sl": round(sl, 2), "tp": round(tp, 2),
        "exit": round(exit_px, 2), "result": reason,
        "gross_pct": round(gross, 3), "net_pct": round(gross - COST_PCT, 3),
        "net_rs": round((gross - COST_PCT) / 100 * CAPITAL_PER_TRADE, 2),
        "bars_held": j - i,
    }


def stats(t):
    if t.empty:
        return {"trades": 0}
    eq = t["net_rs"].cumsum()
    dd = (eq - eq.cummax()).min()
    wins = t[t["net_pct"] > 0]["net_pct"].sum()
    losses = -t[t["net_pct"] <= 0]["net_pct"].sum()
    return {
        "trades": int(len(t)),
        "win_rate_pct": round((t["net_pct"] > 0).mean() * 100, 1),
        "tp_hits": int((t["result"] == "TP").sum()),
        "sl_hits": int((t["result"] == "SL").sum()),
        "eod_exits": int((t["result"] == "EOD").sum()),
        "avg_gross_pct": round(t["gross_pct"].mean(), 3),
        "avg_net_pct": round(t["net_pct"].mean(), 3),
        "total_net_pct_sum": round(t["net_pct"].sum(), 2),
        "total_net_rs": round(t["net_rs"].sum(), 0),
        "profit_factor": round(wins / losses, 2) if losses > 0 else None,
        "max_drawdown_rs": round(float(dd), 0),
    }


def main():
    files = sys.argv[1:] or sorted(glob.glob(str(ROOT / "data" / "angel_5m" / "*_5m.csv"))) \
        or sorted(glob.glob(str(ROOT / "data" / "5m" / "*_5m.csv")))
    if not files:
        raise SystemExit("No candle CSVs found. Run download_angel_5m_6m.py first.")
    out_dir = ROOT / "data" / "backtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    trades, first, last = [], None, None
    for n, f in enumerate(files, 1):
        sym = Path(f).name.replace("_5m.csv", "")
        df = load_csv(f)
        if len(df) < 100:
            continue
        first = min(first, df["time"].iloc[0]) if first is not None else df["time"].iloc[0]
        last = max(last, df["time"].iloc[-1]) if last is not None else df["time"].iloc[-1]
        d = add_indicators(df)
        for s in find_signals(d):
            trades.append({"symbol": sym, **simulate(d, s)})
        if n % 25 == 0 or n == len(files):
            print(f"PROGRESS {n}/{len(files)} | trades={len(trades)}", flush=True)

    t = pd.DataFrame(trades)
    if t.empty:
        print("No trades generated.")
        return
    t = t.sort_values("entry_time").reset_index(drop=True)
    t.to_csv(out_dir / "trades.csv", index=False)
    t["month"] = t["entry_time"].str[:7]
    monthly = t.groupby("month").apply(lambda g: pd.Series(stats(g))).reset_index()
    monthly.to_csv(out_dir / "monthly.csv", index=False)
    per_sym = t.groupby("symbol").apply(lambda g: pd.Series(stats(g))).reset_index() \
        .sort_values("total_net_rs", ascending=False)
    per_sym.to_csv(out_dir / "per_symbol.csv", index=False)
    summary = {
        "period": f"{first:%Y-%m-%d} to {last:%Y-%m-%d}",
        "symbols_tested": len(files),
        "cost_round_trip_pct": COST_PCT,
        "capital_per_trade_rs": CAPITAL_PER_TRADE,
        "ALL": stats(t),
        "LONG": stats(t[t["side"] == "LONG"]),
        "SHORT": stats(t[t["side"] == "SHORT"]),
        "by_zone": {z: stats(g) for z, g in t.groupby("zone")},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print("BACKTEST SUMMARY\n" + json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
