"""Performance metrics on a trade list (net of all costs)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def max_losing_streak(pnl) -> int:
    best = cur = 0
    for x in pnl:
        cur = cur + 1 if x < 0 else 0
        best = max(best, cur)
    return best


def equity_drawdown(trades: pd.DataFrame, capital: float) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["exit_time", "equity", "drawdown", "drawdown_pct"])
    t = trades.sort_values("exit_time")
    eq = capital + t["net"].cumsum()
    peak = eq.cummax()
    return pd.DataFrame({"exit_time": t["exit_time"].values, "equity": eq.values,
                         "drawdown": (eq - peak).values, "drawdown_pct": ((eq - peak) / peak * 100).values})


def summarize(trades: pd.DataFrame, capital: float = 500000.0, total_bars: int | None = None) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    net = trades["net"].to_numpy()
    R = trades["R"].to_numpy()
    wins, losses = net[net > 0], net[net <= 0]
    gp, gl = wins.sum(), -losses.sum()
    ed = equity_drawdown(trades, capital)
    t_stat, p = (np.nan, np.nan)
    if n > 2 and R.std(ddof=1) > 0:
        t_stat = R.mean() / (R.std(ddof=1) / np.sqrt(n))
        p = float(1 - stats.t.cdf(t_stat, df=n - 1))                 # one-sided: mean R > 0
    months = trades.groupby(pd.to_datetime(trades["date"]).dt.to_period("M"))["net"].sum()
    bars_held = (trades["exit_slot"] - trades["entry_slot"] + 1).clip(lower=1).sum()
    return {
        "trades": n,
        "net_pnl": float(net.sum()),
        "gross_pnl_before_costs": float(trades["gross"].sum()),
        "charges": float(trades["charges"].sum()),
        "slippage": float(trades["slippage"].sum()),
        "win_rate": float((net > 0).mean() * 100),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(gp / gl) if gl > 0 else float("inf"),
        "expectancy_rs": float(net.mean()),
        "expectancy_R": float(R.mean()),
        "max_drawdown": float(ed["drawdown"].min()),
        "max_drawdown_pct": float(ed["drawdown_pct"].min()),
        "max_losing_streak": int(max_losing_streak(net)),
        "exposure_pct": float(bars_held / total_bars * 100) if total_bars else np.nan,
        "months": int(len(months)),
        "positive_month_frac": float((months > 0).mean()) if len(months) else np.nan,
        "t_stat": float(t_stat) if np.isfinite(t_stat) else np.nan,
        "p_value": p,
        "symbols_traded": int(trades["symbol"].nunique()),
    }


def bh_fdr(pvals: np.ndarray, alpha: float) -> np.ndarray:
    """Benjamini-Hochberg: boolean 'significant' for each p (NaN -> False)."""
    p = np.asarray(pvals, float)
    ok = np.isfinite(p)
    out = np.zeros(len(p), dtype=bool)
    if ok.sum() == 0:
        return out
    pv = p[ok]
    order = np.argsort(pv)
    m = len(pv)
    thresh = alpha * (np.arange(1, m + 1) / m)
    passed = pv[order] <= thresh
    k = np.nonzero(passed)[0].max() + 1 if passed.any() else 0
    sig = np.zeros(m, dtype=bool)
    sig[order[:k]] = True
    out[np.nonzero(ok)[0]] = sig
    return out
