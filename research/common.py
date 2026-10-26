"""Shared helpers: config, paths, logging, symbol list, data loading."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def load_config(path: str | os.PathLike | None = None) -> dict:
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_logger(name: str = "research", log_file: Path | None = None) -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        log.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)
    if log_file is not None and not any(isinstance(h, logging.FileHandler) for h in log.handlers):
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
    return log


def load_universe(cfg: dict) -> list[str]:
    path = ROOT / cfg["universe_file"]
    names = [x.strip().upper().replace(".NS", "").replace("-EQ", "")
             for x in path.read_text(encoding="utf-8-sig").splitlines() if x.strip()]
    return list(dict.fromkeys(names))


def raw_path(cfg: dict, symbol: str) -> Path:
    return ROOT / cfg["data"]["raw_dir"] / f"{symbol}.csv.gz"


def read_raw(cfg: dict, symbol: str) -> pd.DataFrame | None:
    """Raw 5m candles (naive IST timestamps). Returns None if not present."""
    p = raw_path(cfg, symbol)
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["datetime"])
    return df[["datetime", "open", "high", "low", "close", "volume"]]


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def _default(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if np.isnan(o) else float(o)
        if isinstance(o, (pd.Timestamp,)):
            return o.isoformat()
        return str(o)

    path.write_text(json.dumps(obj, indent=2, default=_default), encoding="utf-8")


def hhmm_to_slot(hhmm: str, first_bar: str = "09:15") -> int:
    h, m = map(int, hhmm.split(":"))
    h0, m0 = map(int, first_bar.split(":"))
    return ((h * 60 + m) - (h0 * 60 + m0)) // 5
