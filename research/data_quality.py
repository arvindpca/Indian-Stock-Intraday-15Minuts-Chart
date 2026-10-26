"""Symbol-level data validation + cleaning.

Cleaning rules (every change is COUNTED in the report, nothing silently replaced):
  * duplicate timestamps -> keep first
  * invalid OHLC (<=0, high<max(o,c), low>min(o,c), NaN) -> bar dropped
  * bars outside 09:15-15:25 or on weekends -> dropped
  * missing bars are NOT filled (indicators run on available bars)
  * special sessions (very few bars, e.g. Muhurat) -> day marked no-trade
  * overnight gap > corp_action_gap_pct -> day marked no-trade (suspected split/bonus)
15m data is derived by resampling the validated 5m data (consistency check in tests).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from research.common import read_raw

QUALITY_COLS = ["symbol", "status", "reasons", "rows_raw", "first", "last", "trading_days", "market_days_missing",
                "dup_ts", "invalid_ohlc", "out_of_session", "auction_bars_dropped", "zero_volume_bars", "missing_bars", "complete_day_pct",
                "special_session_days", "corp_action_suspect_days", "max_bar_jump_pct", "median_turnover_cr"]


def clean_and_validate(df: pd.DataFrame, symbol: str, cfg: dict, market_days: set | None = None,
                       is_index: bool = False):
    q = cfg["quality"]
    rep = {k: None for k in QUALITY_COLS}
    rep.update(symbol=symbol, rows_raw=len(df))
    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    if getattr(df["datetime"].dt, "tz", None) is not None:
        df["datetime"] = df["datetime"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    start = datetime.now() - timedelta(days=cfg["data"]["history_days"])
    df = df[df["datetime"] >= start]
    df = df.sort_values("datetime", kind="stable")

    dup = df["datetime"].duplicated()
    rep["dup_ts"] = int(dup.sum())
    df = df[~dup]

    o, h, l, c = (df[k] for k in ("open", "high", "low", "close"))
    bad = (df[["open", "high", "low", "close"]].isna().any(axis=1) | (o <= 0) | (l <= 0)
           | (h < np.maximum(o, c) - 1e-9) | (l > np.minimum(o, c) + 1e-9) | (h < l))
    rep["invalid_ohlc"] = int(bad.sum())
    df = df[~bad]

    t = df["datetime"].dt.hour * 60 + df["datetime"].dt.minute
    oos = (t < 9 * 60 + 15) | (t > 15 * 60 + 25) | (df["datetime"].dt.weekday >= 5) | (df["datetime"].dt.minute % 5 != 0)
    rep["out_of_session"] = int(oos.sum())
    df = df[~oos].reset_index(drop=True)
    if not is_index and q.get("drop_auction_bars_after"):
        hh, mm = map(int, q["drop_auction_bars_after"].split(":"))
        tt = df["datetime"].dt.hour * 60 + df["datetime"].dt.minute
        auc = (tt >= hh * 60 + mm) & (df["volume"] <= 0)
        rep["auction_bars_dropped"] = int(auc.sum())
        df = df[~auc].reset_index(drop=True)
    rep["zero_volume_bars"] = int((df["volume"] <= 0).sum())

    if df.empty:
        rep.update(status="FAIL", reasons="no valid bars")
        return None, rep

    df["date"] = df["datetime"].dt.normalize()
    per_day = df.groupby("date").size()
    special = per_day[per_day <= q["special_session_max_bars"]].index
    normal = per_day.drop(special)
    rep["special_session_days"] = len(special)
    rep["missing_bars"] = int((q["bars_per_day"] - normal.clip(upper=q["bars_per_day"])).sum())
    rep["complete_day_pct"] = round(100 * (normal >= q["bars_per_day"]).mean(), 2) if len(normal) else 0.0

    daily = df.groupby("date").agg(o=("open", "first"), c=("close", "last"))
    daily["tv"] = (df["close"] * df["volume"]).groupby(df["date"]).sum()
    gap = (daily["o"] / daily["c"].shift() - 1).abs() * 100
    ca_days = set(gap[gap > q["corp_action_gap_pct"]].index)
    rep["corp_action_suspect_days"] = ";".join(d.strftime("%Y-%m-%d") for d in sorted(ca_days))
    jump = (df["close"] / df["close"].shift() - 1).abs() * 100
    same_day = df["date"] == df["date"].shift()
    rep["max_bar_jump_pct"] = round(float(jump[same_day].max() or 0), 2)

    df["no_trade_day"] = df["date"].isin(set(special) | ca_days)
    rep["first"] = df["datetime"].min()
    rep["last"] = df["datetime"].max()
    rep["trading_days"] = int(len(normal))
    if market_days:
        own = set(per_day.index)
        rng = {d for d in market_days if rep["first"].normalize() <= d <= rep["last"].normalize()}
        rep["market_days_missing"] = len(rng - own)
    rep["median_turnover_cr"] = None if is_index else round(float(daily["tv"].median() / 1e7), 2)

    reasons = []
    if rep["trading_days"] < q["min_trading_days"]:
        reasons.append(f"only {rep['trading_days']} days")
    if not is_index and (rep["median_turnover_cr"] or 0) < q["min_median_turnover_cr"]:
        reasons.append(f"illiquid: median turnover {rep['median_turnover_cr']} cr")
    warns = []
    if rep["complete_day_pct"] < 90:
        warns.append(f"complete days {rep['complete_day_pct']}%")
    if (rep["market_days_missing"] or 0) > 5:
        warns.append(f"{rep['market_days_missing']} market days missing")
    if ca_days:
        warns.append("corporate-action-like gaps")
    if rep["max_bar_jump_pct"] > q["max_bar_jump_pct"]:
        warns.append(f"bar jump {rep['max_bar_jump_pct']}%")
    rep["status"] = "FAIL" if reasons else ("WARN" if warns else "PASS")
    rep["reasons"] = "; ".join(reasons + warns)
    return df.drop(columns=["date"]), rep


def resample_15m(df5: pd.DataFrame) -> pd.DataFrame:
    """15m candles from 5m, anchored at 09:15 (09:15, 09:30, ...)."""
    d = df5.set_index("datetime")
    out = d.resample("15min", origin="start_day", offset="15min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["open"])
    return out.reset_index()


def validate_universe(cfg: dict, symbols: list[str], log):
    """Returns (clean_data dict, quality DataFrame)."""
    market = cfg["market_symbol"]
    mkt_raw = read_raw(cfg, market)
    market_days = set(pd.to_datetime(mkt_raw["datetime"]).dt.normalize().unique()) if mkt_raw is not None else None
    clean, rows = {}, []
    for sym in [market] + [s for s in symbols if s != market]:
        raw = read_raw(cfg, sym)
        if raw is None:
            rows.append({"symbol": sym, "status": "MISSING", "reasons": "no data file (not downloaded / no token)"})
            continue
        df, rep = clean_and_validate(raw, sym, cfg, market_days, is_index=(sym == market))
        rows.append(rep)
        if df is not None and rep["status"] != "FAIL":
            clean[sym] = df
    qdf = pd.DataFrame(rows, columns=QUALITY_COLS)
    log.info("quality: %s", qdf["status"].value_counts().to_dict())
    return clean, qdf
