#!/usr/bin/env python3
"""End-to-end research pipeline (data quality -> features -> discovery -> validation ->
walk-forward -> untouched OOS -> report). Usage: see research/README.md
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from research.common import ROOT, get_logger, load_config, load_universe, write_json  # noqa: E402
from research.data_download import seed_from_repo  # noqa: E402
from research.data_quality import resample_15m, validate_universe  # noqa: E402
from research.discovery import Researcher, config_hash  # noqa: E402
from research.engine import ExitCache, build_panel, portfolio_sim, run_strategy  # noqa: E402
from research.features import build_features, filter_names, trigger_names  # noqa: E402
from research.metrics import equity_drawdown, summarize  # noqa: E402
from research.walkforward import walk_forward  # noqa: E402

FEAT_VERSION_FILES = ["features.py"]


def _feat_job(args):
    sym, df, cfg, market = args
    return sym, build_features(df, cfg, market)


def features_with_cache(clean, cfg, log, workers):
    cache_dir = ROOT / "research_data" / "cache" / "features"
    cache_dir.mkdir(parents=True, exist_ok=True)
    src = "".join((Path(__file__).parent / f).read_text() for f in FEAT_VERSION_FILES)
    h = config_hash({k: cfg[k] for k in ("discovery", "session", "data")}, src)
    market = clean.get(cfg["market_symbol"])
    out, jobs = {}, []
    for sym, df in clean.items():
        if sym == cfg["market_symbol"]:
            continue
        p = cache_dir / f"{sym}_{h}.pkl"
        if p.exists():
            out[sym] = pd.read_pickle(p)
        else:
            jobs.append((sym, df, cfg, market))
    log.info("features: %d cached, %d to build (workers=%d)", len(out), len(jobs), workers)
    if jobs:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for sym, f in ex.map(_feat_job, jobs, chunksize=4):
                f.to_pickle(cache_dir / f"{sym}_{h}.pkl")
                out[sym] = f
    return out


def period_masks(P, cfg):
    days = np.array(sorted(np.unique(P.dates)))
    n = len(days)
    a = int(n * cfg["splits"]["dev"])
    b = int(n * (cfg["splits"]["dev"] + cfg["splits"]["val"]))
    edges = {"dev": (days[0], days[a - 1]), "val": (days[a], days[b - 1]), "oos": (days[b], days[-1])}
    m = {k: (P.dates >= lo) & (P.dates <= hi) for k, (lo, hi) in edges.items()}
    m["devval"] = m["dev"] | m["val"]
    return m, {k: (str(lo)[:10], str(hi)[:10]) for k, (lo, hi) in edges.items()}


def breakdowns(trades: pd.DataFrame, cfg) -> dict[str, pd.DataFrame]:
    if trades.empty:
        return {}
    t = trades.copy()
    t["month"] = pd.to_datetime(t["date"]).dt.to_period("M").astype(str)
    t["time_bucket"] = pd.cut(t["signal_slot"], [-1, 20, 50, 75], labels=["09:30-11:00", "11:00-13:30", "13:30-14:40"]).astype(str)
    out = {}
    for col in ("month", "symbol", "direction", "vol_regime", "time_bucket"):
        g = t.groupby(col)
        out[col] = pd.DataFrame({"trades": g.size(), "net_pnl": g["net"].sum(), "win_rate": g["net"].apply(lambda x: (x > 0).mean() * 100),
                                 "expectancy_R": g["R"].mean(), "charges": g["charges"].sum()}).reset_index()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="comma list to restrict universe (e.g. RELIANCE for local proof)")
    ap.add_argument("--min-trades-dev", type=int, default=None)
    ap.add_argument("--min-trades-val", type=int, default=None)
    ap.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    cfg = load_config()
    if args.min_trades_dev:
        cfg["discovery"]["min_trades_dev"] = args.min_trades_dev
    if args.min_trades_val:
        cfg["discovery"]["min_trades_val"] = args.min_trades_val
    run_id = datetime.now().strftime("%Y-%m-%d_%H%M")
    out = Path(args.out) if args.out else ROOT / "research_results" / run_id
    out.mkdir(parents=True, exist_ok=True)
    log = get_logger("research", out / "run.log")
    t0 = time.time()
    shutil.copy(Path(__file__).parent / "config.yaml", out / "config_used.yaml")
    write_json(out / "cli_overrides.json", vars(args))

    # ---------------------------------------------------------------- 1. data
    seed_from_repo(cfg)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or load_universe(cfg)
    clean, quality = validate_universe(cfg, symbols, log)
    quality.to_csv(out / "data_quality.csv", index=False)
    dl = ROOT / cfg["data"]["report_dir"] / "download_report.csv"
    if dl.exists():
        shutil.copy(dl, out / "download_report.csv")
    # 15m consistency: derived 15m vs repo's independently downloaded RELIANCE_15m (if available)
    chk15 = {}
    ref15 = ROOT / "reliance_strategy/data/RELIANCE_15m.csv"
    if "RELIANCE" in clean and ref15.exists():
        r15 = pd.read_csv(ref15, parse_dates=["datetime"])
        d15 = resample_15m(clean["RELIANCE"].drop(columns=["no_trade_day"]))
        mrg = r15.merge(d15, on="datetime", suffixes=("_ref", "_drv"))
        chk15 = {"overlap_bars": len(mrg), **{f"max_abs_diff_{k}": float((mrg[f"{k}_ref"] - mrg[f"{k}_drv"]).abs().max())
                                              for k in ("open", "high", "low", "close")},
                 "volume_match_pct": float((mrg["volume_ref"] == mrg["volume_drv"]).mean() * 100)}
    tradable_syms = [s for s in clean if s != cfg["market_symbol"]]
    write_json(out / "universe_summary.json", {
        "requested": len(symbols), "quality_status": quality["status"].value_counts().to_dict(),
        "usable_symbols": len(tradable_syms), "market_data": cfg["market_symbol"] in clean,
        "check_15m_vs_reference": chk15,
        "known_limitations": [
            "Universe = today's stock_names.txt -> survivorship bias (delisted/renamed stocks absent)",
            "Angel candles appear split/bonus adjusted (no large gaps in RELIANCE 2024 bonus); large gaps are flagged, not fixed",
            "No sector map in repo -> sector breakdown not reported",
            "No F&O contract-level data -> no options backtest (stock OHLCV is NOT option data)"]})
    if not tradable_syms:
        log.error("no usable symbols - stopping")
        return

    # ---------------------------------------------------------------- 2. features + panel
    feats = features_with_cache({s: clean[s] for s in tradable_syms + [cfg["market_symbol"]] if s in clean}, cfg, log, args.workers)
    P = build_panel(feats, cfg)
    del feats
    masks, edges = period_masks(P, cfg)
    log.info("panel rows=%d (symbol-days) | splits %s", len(P.symbols), edges)
    cache = ExitCache(P)
    first = next(iter(P.feats))
    triggers = sorted({k[2:-2] for k in P.feats if k.startswith("T_")})
    filters = sorted({k[2:-2] for k in P.feats if k.startswith("F_")})
    del first

    # ---------------------------------------------------------------- 3. discovery (DEV only)
    R = Researcher(P, cache, cfg, masks, log)
    dev = R.run(triggers, filters)
    dev.to_csv(out / "experiments_dev.csv", index=False)
    write_json(out / "experiment_space.json", {"triggers": triggers, "filters": filters, "exits": cfg["exits"],
                                                "n_dev_experiments": len(dev), "splits": edges})

    # ---------------------------------------------------------------- 4. validation
    val = R.validate(dev)
    val.to_csv(out / "validation.csv", index=False)
    log.info("validation: %d candidates, %d accepted, %d strict-accepted", len(val),
             int(val["accepted"].sum()) if len(val) else 0, int(val["strict_accepted"].sum()) if len(val) else 0)

    # ---------------------------------------------------------------- 5. walk-forward (dev+val)
    folds, wf_trades, wf_sum = walk_forward(P, cache, cfg, triggers, masks["devval"], log)
    folds.to_csv(out / "walkforward_folds.csv", index=False)
    wf_trades.to_csv(out / "walkforward_trades.csv", index=False)
    write_json(out / "walkforward_summary.json", wf_sum)

    # ---------------------------------------------------------------- 6. baselines (all periods, transparency)
    base_rows = []
    for trig in ("OPEN", "NBAR_BRK"):
        for side in "LS":
            d = dev[(dev.trigger == trig) & (dev.side == side) & (dev.filters == "-")].sort_values("expectancy_R", ascending=False)
            if d.empty:
                continue
            b = d.iloc[0]
            for per in ("dev", "val", "oos"):
                rec = R.evaluate("baseline", trig, [], side, b.sl_atr, b.rr, period=per)
                base_rows.append(rec)
    pd.DataFrame(base_rows).to_csv(out / "baselines.csv", index=False)

    # ---------------------------------------------------------------- 7. untouched OOS (accepted only)
    accepted = val[val["accepted"]] if len(val) else val
    shown = accepted
    label = "ACCEPTED"
    if accepted is None or accepted.empty:
        label = "REJECTED_TRANSPARENCY_ONLY"
        shown = val.groupby("side").head(1) if len(val) else val
    oos_rows, oos_trades = [], []
    for r in (shown.itertuples() if len(shown) else []):
        filt = [] if r.filters == "-" else r.filters.split("+")
        rec, t = R.evaluate("oos", r.trigger, filt, r.side, r.sl_atr, r.rr, period="oos", keep_trades=True)
        rec["status"] = label
        sens = {}
        for mult in (0, 1, 2, 3):
            sens[f"oos_expR_slip{mult}x"] = R.evaluate("cost", r.trigger, filt, r.side, r.sl_atr, r.rr, period="oos", slip_mult=mult).get("expectancy_R")
        rec.update(sens)
        oos_rows.append(rec)
        if len(t):
            t["strategy"] = r.id
            oos_trades.append(t)
    oos_df = pd.DataFrame(oos_rows)
    oos_df.to_csv(out / "oos_results.csv", index=False)
    all_oos = pd.concat(oos_trades, ignore_index=True) if oos_trades else pd.DataFrame()
    all_oos.to_csv(out / "oos_trades.csv", index=False)
    port = portfolio_sim(all_oos, cfg) if len(all_oos) else all_oos
    port.to_csv(out / "portfolio_oos_trades.csv", index=False)
    equity_drawdown(port, cfg["risk"]["capital"]).to_csv(out / "equity_drawdown_oos.csv", index=False)
    oos_days = int(np.unique(P.dates[masks["oos"]]).size)
    port_sum = summarize(port, cfg["risk"]["capital"], total_bars=oos_days * 75 * max(1, cfg["risk"]["max_positions"])) if len(port) else {"trades": 0}
    for name, df in breakdowns(port, cfg).items():
        df.to_csv(out / f"oos_breakdown_by_{name}.csv", index=False)

    # ---------------------------------------------------------------- 8. summary
    top_dev = dev[dev.trades >= cfg["discovery"]["min_trades_dev"]].sort_values("expectancy_R", ascending=False).head(10)
    top_dev.to_csv(out / "ranked_dev_top10.csv", index=False)
    summary = {
        "run_id": run_id, "runtime_min": round((time.time() - t0) / 60, 2), "splits": edges,
        "symbols_used": len(tradable_syms), "n_dev_experiments": len(dev),
        "n_fdr_significant_dev": int(dev["fdr_significant"].sum()),
        "validation_candidates": len(val), "accepted": int(val["accepted"].sum()) if len(val) else 0,
        "strict_accepted": int(val["strict_accepted"].sum()) if len(val) else 0,
        "oos_status": label, "oos_strategies": oos_df.to_dict("records"), "oos_portfolio": port_sum,
        "walkforward": wf_sum, "verdict": None}
    robust = (label == "ACCEPTED" and port_sum.get("trades", 0) >= 50 and port_sum.get("expectancy_R", -1) > 0
              and (wf_sum.get("combined", {}).get("expectancy_R") or -1) > 0)
    summary["verdict"] = ("CANDIDATE FOR PAPER TRADING (not live)" if robust else
                          "NO ROBUST PROFITABLE STRATEGY FOUND under these rules/costs")
    write_json(out / "summary.json", summary)
    log.info("VERDICT: %s | runtime %.1f min | results -> %s", summary["verdict"], summary["runtime_min"], out)


if __name__ == "__main__":
    main()
