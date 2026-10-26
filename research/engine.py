"""Backtest engine on a (symbol-day x 5min-slot) panel.

Execution model (conservative, tested in tests/):
  * signal on COMPLETED bar j  -> entry at OPEN of bar j+1 (same day only)
  * stop = entry -/+ sl_atr * ATR(j); target = entry +/- rr * risk (rr=0 -> no target)
  * SL/TP checked from the entry bar itself; if SL and TP fall in the SAME bar -> SL assumed
  * gap through stop -> filled at that bar's open (worse); target filled exactly at target
  * no TP/SL -> exit at OPEN of the square-off bar (15:15), or last available close before it
  * one open position per symbol: a new entry must come after the previous exit bar
  * slippage % on every fill + full Angel One intraday charges
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from research.common import hhmm_to_slot
from research.costs import intraday_charges

NSLOT = 75
SL_HIT, TP_HIT, EOD = 1, 2, 3


@dataclass
class Panel:
    symbols: np.ndarray        # (N,) symbol per row
    dates: np.ndarray          # (N,) datetime64 day per row
    O: np.ndarray
    H: np.ndarray
    L: np.ndarray
    C: np.ndarray
    ATR: np.ndarray
    AVGV: np.ndarray
    vol_regime: np.ndarray     # (N,75) object
    tradable: np.ndarray       # (N,75) bool : signal at slot j allowed
    feats: dict                # name -> (N,75) bool
    js: int
    je: int
    sq: int


def build_panel(feature_frames: dict[str, pd.DataFrame], cfg: dict) -> Panel:
    rows_sym, rows_date, blocks = [], [], []
    for sym, f in feature_frames.items():
        days = f["date"].unique()
        rows_sym.extend([sym] * len(days))
        rows_date.extend(days)
        blocks.append((f, days))
    N = len(rows_sym)
    feat_cols = [c for c in next(iter(feature_frames.values())).columns if c[:2] in ("T_", "F_")]

    def nanarr():
        return np.full((N, NSLOT), np.nan, dtype=np.float64)

    O, H, L, C, A, V = nanarr(), nanarr(), nanarr(), nanarr(), nanarr(), nanarr()
    VR = np.full((N, NSLOT), "na", dtype=object)
    NT = np.zeros((N, NSLOT), dtype=bool)
    feats = {c: np.zeros((N, NSLOT), dtype=bool) for c in feat_cols}
    start = 0
    for f, days in blocks:
        f = f[(f["slot"] >= 0) & (f["slot"] < NSLOT)]
        ridx = start + pd.Index(days).get_indexer(f["date"])
        s = f["slot"].to_numpy()
        for arr, col in ((O, "open"), (H, "high"), (L, "low"), (C, "close"), (A, "atr"), (V, "avgvol20")):
            arr[ridx, s] = f[col].to_numpy(dtype=float)
        VR[ridx, s] = f["vol_regime"].to_numpy()
        NT[ridx, s] = f["no_trade_day"].to_numpy(dtype=bool)
        for c in feat_cols:
            feats[c][ridx, s] = f[c].to_numpy(dtype=bool)
        start += len(days)

    js = hhmm_to_slot(cfg["session"]["entry_start"])
    je = hhmm_to_slot(cfg["session"]["entry_end"])
    sq = hhmm_to_slot(cfg["session"]["square_off"])
    je = min(je, sq - 2)
    tradable = np.zeros((N, NSLOT), dtype=bool)
    nxt_open = np.concatenate([O[:, 1:], np.full((N, 1), np.nan)], axis=1)
    tradable[:, js:je + 1] = True
    tradable &= ~NT & np.isfinite(A) & (A > 0) & np.isfinite(C) & np.isfinite(nxt_open)
    return Panel(np.array(rows_sym), np.array(rows_date, dtype="datetime64[ns]"), O, H, L, C, A, V, VR,
                 tradable, feats, js, je, sq)


def eod_price(P: Panel) -> np.ndarray:
    """Exit price when nothing hit: open of square-off bar, else last close before it."""
    px = P.O[:, P.sq].copy()
    lastc = pd.DataFrame(P.C[:, :P.sq]).ffill(axis=1).iloc[:, -1].to_numpy()
    return np.where(np.isfinite(px), px, lastc)


def precompute_exits(P: Panel, direction: int, sl_atr: float, rr: float, chunk: int = 1500):
    """For every possible signal slot j: exit slot, raw exit price, reason. Shapes (N,75)."""
    N = len(P.symbols)
    J = np.arange(P.js, P.je + 1)
    K = np.arange(NSLOT)
    valid = (K[None, :] >= (J[:, None] + 1)) & (K[None, :] < P.sq)          # (J,K)
    xs = np.full((N, NSLOT), -1, dtype=np.int16)
    xp = np.full((N, NSLOT), np.nan)
    rs = np.zeros((N, NSLOT), dtype=np.int8)
    eod = eod_price(P)
    BIG = 10_000
    for a in range(0, N, chunk):
        b = min(N, a + chunk)
        O, H, L = P.O[a:b], P.H[a:b], P.L[a:b]
        E = O[:, J + 1]                                   # entry (raw) at next open
        R = sl_atr * P.ATR[a:b][:, J]
        sl = E - direction * R
        tp = E + direction * rr * R if rr > 0 else None
        with np.errstate(invalid="ignore"):
            if direction == 1:
                hit_sl = (L[:, None, :] <= sl[:, :, None]) & valid[None]
                hit_tp = (H[:, None, :] >= tp[:, :, None]) & valid[None] if tp is not None else None
            else:
                hit_sl = (H[:, None, :] >= sl[:, :, None]) & valid[None]
                hit_tp = (L[:, None, :] <= tp[:, :, None]) & valid[None] if tp is not None else None
        k_sl = np.where(hit_sl.any(2), hit_sl.argmax(2), BIG)
        k_tp = np.where(hit_tp.any(2), hit_tp.argmax(2), BIG) if hit_tp is not None else np.full_like(k_sl, BIG)
        is_sl = (k_sl <= k_tp) & (k_sl < BIG)                 # tie -> SL (conservative)
        is_tp = (k_tp < k_sl)
        k_exit = np.where(is_sl, k_sl, np.where(is_tp, k_tp, P.sq))
        o_at = np.take_along_axis(O, np.clip(k_exit, 0, NSLOT - 1), axis=1)
        if direction == 1:
            sl_fill = np.where(np.isfinite(o_at), np.minimum(sl, o_at), sl)
        else:
            sl_fill = np.where(np.isfinite(o_at), np.maximum(sl, o_at), sl)
        price = np.where(is_sl, sl_fill, np.where(is_tp, tp if tp is not None else np.nan, eod[a:b, None]))
        reason = np.where(is_sl, SL_HIT, np.where(is_tp, TP_HIT, EOD))
        xs[a:b, J] = k_exit
        xp[a:b, J] = price
        rs[a:b, J] = reason
    return xs, xp, rs


def select_non_overlapping(mask: np.ndarray, exit_slot: np.ndarray, js: int, je: int):
    """Greedy chronological selection per symbol-day. Returns (rows, signal_slots)."""
    N = mask.shape[0]
    busy = np.full(N, -1, dtype=np.int32)
    rows, slots = [], []
    for j in range(js, je + 1):
        take = mask[:, j] & ((j + 1) > busy)
        if take.any():
            r = np.nonzero(take)[0]
            busy[r] = exit_slot[r, j]
            rows.append(r)
            slots.append(np.full(len(r), j, dtype=np.int16))
    if not rows:
        return np.array([], dtype=int), np.array([], dtype=np.int16)
    return np.concatenate(rows), np.concatenate(slots)


def make_trades(P: Panel, rows, slots, direction, sl_atr, rr, exits, cfg, slip_mult: float = 1.0) -> pd.DataFrame:
    xs, xp, rs = exits
    risk, costs = cfg["risk"], cfg["costs"]
    if len(rows) == 0:
        return pd.DataFrame()
    E = P.O[rows, slots + 1]
    Rps = sl_atr * P.ATR[rows, slots]
    X = xp[rows, slots]
    qty = np.floor(risk["risk_per_trade"] / Rps)
    qty = np.minimum(qty, np.floor(risk["max_notional"] / E))
    qty = np.minimum(qty, np.floor(risk["max_vol_participation"] * np.nan_to_num(P.AVGV[rows, slots], nan=0.0)))
    ok = (qty >= 1) & np.isfinite(X)
    rows, slots, E, Rps, X, qty = rows[ok], slots[ok], E[ok], Rps[ok], X[ok], qty[ok]
    s = costs["slippage_pct"] / 100 * slip_mult
    e_fill = E * (1 + direction * s)
    x_fill = X * (1 - direction * s)
    gross = direction * (x_fill - e_fill) * qty
    buy_v = np.where(direction == 1, e_fill, x_fill) * qty
    sell_v = np.where(direction == 1, x_fill, e_fill) * qty
    charges = intraday_charges(buy_v, sell_v, costs)
    net = gross - charges
    t = pd.DataFrame({
        "symbol": P.symbols[rows], "date": P.dates[rows], "signal_slot": slots, "entry_slot": slots + 1,
        "exit_slot": xs[rows, slots], "direction": np.where(direction == 1, "LONG", "SHORT"),
        "entry_raw": E, "exit_raw": X, "entry_fill": e_fill, "exit_fill": x_fill, "qty": qty,
        "risk_rs": qty * Rps, "gross": direction * (X - E) * qty, "slippage": direction * (X - E) * qty - gross,
        "charges": charges, "net": net, "R": net / (qty * Rps), "reason": rs[rows, slots],
        "vol_regime": P.vol_regime[rows, slots]})
    t["entry_time"] = pd.to_datetime(t["date"]) + pd.to_timedelta(555 + 5 * t["entry_slot"].astype(int), unit="m")
    t["exit_time"] = pd.to_datetime(t["date"]) + pd.to_timedelta(555 + 5 * t["exit_slot"].astype(int), unit="m")
    t["reason"] = t["reason"].map({SL_HIT: "SL", TP_HIT: "TP", EOD: "EOD"})
    return t.sort_values(["entry_time", "symbol"]).reset_index(drop=True)


def strategy_mask(P: Panel, trigger: str, filters: list[str], side: str, row_mask: np.ndarray | None = None):
    m = P.feats[f"T_{trigger}_{side}"] & P.tradable
    for fl in filters:
        m = m & P.feats[f"F_{fl}_{side}"]
    if row_mask is not None:
        m = m & row_mask[:, None]
    return m


class ExitCache:
    """Lazily computes & memoises exit tables per (direction, sl, rr)."""

    def __init__(self, P: Panel):
        self.P, self._c = P, {}

    def get(self, direction, sl, rr):
        key = (direction, float(sl), float(rr))
        if key not in self._c:
            self._c[key] = precompute_exits(self.P, direction, sl, rr)
        return self._c[key]


def run_strategy(P, cache: ExitCache, cfg, trigger, filters, side, sl, rr, row_mask=None, slip_mult=1.0):
    d = 1 if side == "L" else -1
    ex = cache.get(d, sl, rr)
    m = strategy_mask(P, trigger, filters, side, row_mask)
    rows, slots = select_non_overlapping(m, ex[0], P.js, P.je)
    return make_trades(P, rows, slots, d, sl, rr, ex, cfg, slip_mult)


def portfolio_sim(trades: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Apply max concurrent positions + daily loss limit chronologically. Returns accepted trades."""
    if trades.empty:
        return trades
    r = cfg["risk"]
    t = trades.sort_values(["entry_time", "symbol", "direction"]).reset_index(drop=True)
    accepted, day = [], None
    for i, row in enumerate(t.itertuples(index=False)):
        if row.date != day:
            day, day_pnl, open_pos, busy = row.date, 0.0, [], {}
        # a position exiting during bar k is treated as still open at the start of bar k (conservative)
        day_pnl += sum(p for ex, p in open_pos if ex < row.entry_time)
        open_pos = [(ex, p) for ex, p in open_pos if ex >= row.entry_time]
        if day_pnl <= -r["daily_loss_limit"] or len(open_pos) >= r["max_positions"]:
            continue
        if row.symbol in busy and busy[row.symbol] >= row.entry_time:
            continue                                   # no long+short / overlapping trades on one symbol
        accepted.append(i)
        open_pos.append((row.exit_time, row.net))
        busy[row.symbol] = row.exit_time
    return t.iloc[accepted].reset_index(drop=True)
