"""Feature library. EVERY value at bar t uses only bars <= t (completed-bar, causal).

Triggers (T_*) = fresh entry events.  Filters (F_*) = state conditions.
Each has a long (_L) and short (_S) boolean column. Swing pivots are only known
k bars after the pivot (no lookahead). Daily regime features use the PREVIOUS day.
Order blocks are NOT implemented: no objective, testable definition was agreed;
FVG / liquidity sweep / MSS cover the SMC "zone" idea with rule-based definitions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.common import hhmm_to_slot


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s, n):
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def rsi(c, n=14):
    d = c.diff()
    up, dn = wilder(d.clip(lower=0), n), wilder((-d).clip(lower=0), n)
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def true_range(h, l, c):
    pc = c.shift()
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def adx(h, l, c, n=14):
    up, dn = h.diff(), -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_ = wilder(true_range(h, l, c), n)
    pdi = 100 * wilder(pd.Series(pdm, index=h.index), n) / atr_
    mdi = 100 * wilder(pd.Series(mdm, index=h.index), n) / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return wilder(dx, n), pdi, mdi


def supertrend_dir(h, l, c, atr_, mult=3.0):
    hl2 = ((h + l) / 2).to_numpy()
    a, cl = atr_.to_numpy(), c.to_numpy()
    n = len(cl)
    up_b, dn_b, d = np.full(n, np.nan), np.full(n, np.nan), np.zeros(n, dtype=np.int8)
    for i in range(n):
        if np.isnan(a[i]):
            continue
        bu, bd = hl2[i] + mult * a[i], hl2[i] - mult * a[i]
        if i == 0 or np.isnan(up_b[i - 1]):
            up_b[i], dn_b[i], d[i] = bu, bd, 1
            continue
        up_b[i] = bu if (bu < up_b[i - 1] or cl[i - 1] > up_b[i - 1]) else up_b[i - 1]
        dn_b[i] = bd if (bd > dn_b[i - 1] or cl[i - 1] < dn_b[i - 1]) else dn_b[i - 1]
        if d[i - 1] == -1 and cl[i] > up_b[i - 1]:
            d[i] = 1
        elif d[i - 1] == 1 and cl[i] < dn_b[i - 1]:
            d[i] = -1
        else:
            d[i] = d[i - 1] if d[i - 1] != 0 else 1
    return pd.Series(d, index=c.index)


def _cross_up(a, b):
    return (a > b) & (a.shift() <= b.shift())


def _cross_dn(a, b):
    return (a < b) & (a.shift() >= b.shift())


def build_features(df: pd.DataFrame, cfg: dict, market: pd.DataFrame | None = None) -> pd.DataFrame:
    """df: clean 5m candles of ONE symbol (datetime, ohlcv, no_trade_day)."""
    dc = cfg["discovery"]
    k, nb = dc["swing_k"], dc["nbar_lookback"]
    f = df.reset_index(drop=True).copy()
    o, h, l, c, v = (f[x].astype(float) for x in ("open", "high", "low", "close", "volume"))
    f["date"] = f["datetime"].dt.normalize()
    f["slot"] = ((f["datetime"].dt.hour * 60 + f["datetime"].dt.minute) - (9 * 60 + 15)) // 5
    day = f["date"]
    first_of_day = day != day.shift()

    # ---- core indicators
    f["atr"] = wilder(true_range(h, l, c), 14)
    e9, e20, e21, e50 = ema(c, 9), ema(c, 20), ema(c, 21), ema(c, 50)
    r = rsi(c, 14)
    macd = ema(c, 12) - ema(c, 26)
    sig = ema(macd, 9)
    adx_, pdi, mdi = adx(h, l, c, 14)
    st = supertrend_dir(h, l, c, wilder(true_range(h, l, c), 10), 3.0)
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std(ddof=0)
    bbu, bbl = mid + 2 * sd, mid - 2 * sd
    pv = ((h + l + c) / 3) * v
    cumv = v.groupby(day).cumsum()
    vwap = pv.groupby(day).cumsum() / cumv.replace(0, np.nan)
    f["avgvol20"] = v.rolling(20).mean().shift(1)
    rvol = v / f["avgvol20"].replace(0, np.nan)
    rng = (h - l).replace(0, np.nan)
    body = (c - o).abs() / rng
    cloc = (c - l) / rng

    # ---- session / daily context (only past info)
    orh = h.where(f["slot"] <= 2).groupby(day).transform("max")
    orl = l.where(f["slot"] <= 2).groupby(day).transform("min")
    orh, orl = orh.where(f["slot"] >= 2), orl.where(f["slot"] >= 2)       # known after 09:25 bar closes
    d = f.groupby("date").agg(dh=("high", "max"), dl=("low", "min"), dc=("close", "last"), do=("open", "first"))
    d["pdh"], d["pdl"], d["pdc"] = d["dh"].shift(), d["dl"].shift(), d["dc"].shift()
    d["gap_pct"] = (d["do"] / d["pdc"] - 1) * 100
    d["range_pct_prev"] = ((d["dh"] - d["dl"]) / d["dc"] * 100).shift()
    d["vol_regime_high"] = d["range_pct_prev"] > d["range_pct_prev"].rolling(60, min_periods=20).median()
    d["vol_regime_low"] = d["range_pct_prev"] <= d["range_pct_prev"].rolling(60, min_periods=20).median()
    sma20d = d["dc"].rolling(20).mean()
    d["dtrend_up"] = (d["dc"] > sma20d).shift().astype("boolean").fillna(False).astype(bool)
    d["dtrend_dn"] = (d["dc"] < sma20d).shift().astype("boolean").fillna(False).astype(bool)
    f = f.join(d[["pdh", "pdl", "gap_pct", "vol_regime_high", "vol_regime_low", "dtrend_up", "dtrend_dn"]], on="date")
    pdh, pdl, gap = f["pdh"], f["pdl"], f["gap_pct"]

    # ---- swing structure (pivot confirmed k bars later)
    win = 2 * k + 1
    ph = h.shift(k) == h.rolling(win).max()
    pl = l.shift(k) == l.rolling(win).min()
    sw_hi = h.shift(k).where(ph).ffill()
    sw_lo = l.shift(k).where(pl).ffill()
    sw_hi_prev, sw_lo_prev = sw_hi.shift(), sw_lo.shift()

    T, F = {}, {}
    slot0 = hhmm_to_slot(cfg["session"]["entry_start"])
    T["OPEN"] = (f["slot"] == slot0, f["slot"] == slot0)            # no-filter baseline
    hh, ll = h.rolling(nb).max().shift(), l.rolling(nb).min().shift()
    T["NBAR_BRK"] = (_cross_up(c, hh), _cross_dn(c, ll))            # simple price-action baseline
    orb_l = _cross_up(c, orh) & (f["slot"] >= 3)
    orb_s = _cross_dn(c, orl) & (f["slot"] >= 3)
    T["ORB"] = (orb_l & (orb_l.astype(int).groupby(day).cumsum() == 1),
                orb_s & (orb_s.astype(int).groupby(day).cumsum() == 1))
    T["VWAP_X"] = (_cross_up(c, vwap), _cross_dn(c, vwap))
    T["EMA_X"] = (_cross_up(e9, e21), _cross_dn(e9, e21))
    T["MACD_X"] = (_cross_up(macd, sig), _cross_dn(macd, sig))
    T["ST_FLIP"] = ((st == 1) & (st.shift() == -1), (st == -1) & (st.shift() == 1))
    T["BB_BRK"] = (_cross_up(c, bbu), _cross_dn(c, bbl))
    T["RSI_REV"] = (_cross_up(r, pd.Series(30.0, index=r.index)), _cross_dn(r, pd.Series(70.0, index=r.index)))
    T["PDH_BRK"] = (_cross_up(c, pdh), _cross_dn(c, pdl))
    bos_l, bos_s = _cross_up(c, sw_hi_prev), _cross_dn(c, sw_lo_prev)
    T["BOS"] = (bos_l, bos_s)
    sweep_l = (l < sw_lo_prev) & (c > sw_lo_prev)
    sweep_s = (h > sw_hi_prev) & (c < sw_hi_prev)
    T["SWEEP"] = (sweep_l, sweep_s)
    T["MSS"] = (bos_l & (sweep_l.astype(int).rolling(6).max().shift() == 1),
                bos_s & (sweep_s.astype(int).rolling(6).max().shift() == 1))
    T["FVG"] = (((l - h.shift(2)) > 0.1 * f["atr"]) & (c > o), ((l.shift(2) - h) > 0.1 * f["atr"]) & (c < o))
    lvl_l = hh.where(T["NBAR_BRK"][0]).ffill(limit=6).shift()
    lvl_s = ll.where(T["NBAR_BRK"][1]).ffill(limit=6).shift()
    T["RETEST"] = ((l <= lvl_l + 0.1 * f["atr"]) & (c > lvl_l) & ~T["NBAR_BRK"][0],
                   (h >= lvl_s - 0.1 * f["atr"]) & (c < lvl_s) & ~T["NBAR_BRK"][1])

    ones = pd.Series(True, index=f.index)
    F["EMA50"] = (c > e50, c < e50)
    F["EMA_STACK"] = ((c > e20) & (e20 > e50), (c < e20) & (e20 < e50))
    F["VWAP"] = (c > vwap, c < vwap)
    F["ADX20"] = (adx_ > 20, adx_ > 20)
    F["DI"] = (pdi > mdi, mdi > pdi)
    F["RSI_MOM"] = (r > 55, r < 45)
    F["MACD_POS"] = (macd > sig, macd < sig)
    F["ST"] = (st == 1, st == -1)
    F["BB_IN"] = ((c < bbu) & (c > mid), (c > bbl) & (c < mid))
    F["RVOL15"] = (rvol > 1.5, rvol > 1.5)
    F["VOL_SPIKE"] = (rvol > 2.5, rvol > 2.5)
    F["BODY"] = ((body > 0.6) & (c > o), (body > 0.6) & (c < o))
    F["CLOC"] = (cloc > 0.7, cloc < 0.3)
    F["ABOVE_OR"] = (c > orh, c < orl)
    F["GAP_SMALL"] = (gap.abs() < 0.5, gap.abs() < 0.5)
    F["GAP_WITH"] = (gap > 0.3, gap < -0.3)
    F["GAP_AGAINST"] = (gap < -0.3, gap > 0.3)
    s = f["slot"]
    am, md, pm = hhmm_to_slot("11:00"), hhmm_to_slot("13:30"), hhmm_to_slot("14:40")
    F["T_AM"] = (s < am, s < am)
    F["T_MID"] = ((s >= am) & (s < md), (s >= am) & (s < md))
    F["T_PM"] = ((s >= md) & (s <= pm), (s >= md) & (s <= pm))
    F["VOLREG_HI"] = (f["vol_regime_high"] == True, f["vol_regime_high"] == True)  # noqa: E712
    F["VOLREG_LO"] = (f["vol_regime_low"] == True, f["vol_regime_low"] == True)  # noqa: E712
    F["DTREND"] = (f["dtrend_up"], f["dtrend_dn"])
    atrp = f["atr"] / c * 100
    F["ATR_OK"] = ((atrp > 0.15) & (atrp < 1.0), (atrp > 0.15) & (atrp < 1.0))
    if market is not None and len(market):
        m = market[["datetime", "close"]].copy()
        m["m_e50"] = ema(m["close"].astype(float), 50)
        m["m_up"], m["m_dn"] = m["close"] > m["m_e50"], m["close"] < m["m_e50"]
        mm = f[["datetime"]].merge(m[["datetime", "m_up", "m_dn"]], on="datetime", how="left")
        F["MKT"] = (mm["m_up"].fillna(False).astype(bool).values, mm["m_dn"].fillna(False).astype(bool).values)

    out = f[["datetime", "date", "slot", "open", "high", "low", "close", "volume", "atr", "avgvol20", "no_trade_day"]].copy()
    out["vol_regime"] = np.where(f["vol_regime_high"] == True, "high", np.where(f["vol_regime_low"] == True, "low", "na"))  # noqa: E712
    for name, (a, b) in T.items():
        out[f"T_{name}_L"] = pd.Series(a, index=f.index).fillna(False).astype(bool).values
        out[f"T_{name}_S"] = pd.Series(b, index=f.index).fillna(False).astype(bool).values
    for name, (a, b) in F.items():
        out[f"F_{name}_L"] = pd.Series(np.asarray(a), index=f.index).fillna(False).astype(bool).values
        out[f"F_{name}_S"] = pd.Series(np.asarray(b), index=f.index).fillna(False).astype(bool).values
    del ones, first_of_day
    return out


def trigger_names(feat: pd.DataFrame) -> list[str]:
    return sorted({c[2:-2] for c in feat.columns if c.startswith("T_")})


def filter_names(feat: pd.DataFrame) -> list[str]:
    return sorted({c[2:-2] for c in feat.columns if c.startswith("F_")})
