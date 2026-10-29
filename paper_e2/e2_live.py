#!/usr/bin/env python3
"""
E2 - Sector Pullback (Trend Day): live PAPER trading bot (no real orders).

Daily flow (IST, Tue-Fri; Monday is off by strategy rule):
  09:30  start (Market Clock dispatch) -> pre-market data: daily candles (prev close, 20-day liquidity),
         10-day opening volume baseline, Nifty 20-day trend, today's NSE margin (leverage)
  10:15  decision: Nifty direction + 20-day trend -> strongest 2 sectors -> up to 5 candidate stocks
  10:20-13:35  every 5 min: EMA20 pullback on closed 5-min candles + Nifty 5-min EMA20 + today's POC
               -> paper entry (max 2 a day, one per stock)
  hourly  live P&L of open positions
  15:00  paper exit of everything at LTP, charges, ledger + day summary, state saved

Rules are the backtested "E2" version (see paper_e2/README.md). Telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.
Angel: ANGEL_API_KEY / ANGEL_CLIENT_ID / ANGEL_PASSWORD / ANGEL_TOTP_SECRET (cleaned; never printed).
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "sector_pullback"))
import backtest as bt  # noqa: E402  (charges, _poc, BROKERAGE)

IST = ZoneInfo("Asia/Kolkata")
NIFTY_TOKEN = "99926000"
SCRIP_MASTER = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

STRATEGY = "E2 – Sector Pullback (Trend Day)"
VERSION = "v1.0 (locked 30-Oct-2026)"
STRATEGY_DETAIL = (
    "<b>Strategy kya hai:</b>\n"
    "1️⃣ <b>Bada trend:</b> Nifty kal 20-din EMA ke upar → sirf BUY din, neeche → sirf SHORT din\n"
    "2️⃣ <b>Aaj ki direction (10:15):</b> Nifty kal ke close aur aaj ke open dono ke upar (BUY) / neeche (SHORT)\n"
    "3️⃣ <b>Sector:</b> Nifty 500 me sabse strong 2 sector (median move), Nifty se zyada strong\n"
    "4️⃣ <b>Stock:</b> non-F&amp;O, leverage ≥4x, move 1%–3.5%, volume ≥1.5x, VWAP ke sahi side → top 5\n"
    "5️⃣ <b>Entry (10:15–13:30):</b> 5-min EMA20 pullback candle + Nifty 5-min EMA20 sahi side + aaj ke POC ke sahi side\n"
    "6️⃣ <b>Exit:</b> 3:00 PM (koi fixed SL/target nahi)\n"
    "7️⃣ <b>Risk:</b> max 2 trade/din, ₹10,000 margin × stock leverage per trade, Somvaar off"
)


@dataclass
class Cfg:
    capital: float = 100_000
    margin_per_trade: float = 10_000
    min_leverage: float = 4.0
    max_trades: int = 2
    max_candidates: int = 5
    top_sectors: int = 2
    min_sector_stocks: int = 3
    stock_min: float = 1.0
    stock_max: float = 3.5
    min_rvol: float = 1.5
    min_price: float = 50.0
    min_avg_vol: float = 200_000
    min_turnover_cr: float = 5.0
    regime_ema: int = 20
    ema: int = 20
    touch_tol: float = 0.10
    sl_max_pct: float = 1.5
    slippage_pct: float = 0.03
    decision: str = "10:15"
    last_entry: str = "13:30"
    exit_time: str = "15:00"
    files: dict = field(default_factory=lambda: {
        "universe": ROOT / "sector_pullback/nifty500.txt",
        "tradable": ROOT / "sector_pullback/nifty500_nonfno_lev4.txt",
        "sector_map": ROOT / "sector_pullback/sector_map.csv",
    })


def tmin(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _clean(v):
    v = "".join(c for c in (v or "") if unicodedata.category(c) != "Cf").strip().strip("\"'").strip()
    return v[3:] if v.lower().startswith("bot") and ":" in v else v


def inr(x, sign=False):
    neg = x < 0
    n = str(int(round(abs(x))))
    if len(n) > 3:
        h, t = n[:-3], n[-3:]
        parts = []
        while len(h) > 2:
            parts.insert(0, h[-2:])
            h = h[:-2]
        if h:
            parts.insert(0, h)
        n = ",".join(parts) + "," + t
    return ("-" if neg else ("+" if sign and x > 0 else "")) + "₹" + n


# --------------------------------------------------------------------------- clock / io
class Clock:
    def now(self) -> datetime:
        return datetime.now(IST)

    def sleep_until(self, t: datetime):
        while True:
            left = (t - self.now()).total_seconds()
            if left <= 0:
                return
            time.sleep(min(left, 30))


class Telegram:
    def __init__(self, dry=False):
        self.token, self.chat = _clean(os.getenv("TELEGRAM_BOT_TOKEN")), _clean(os.getenv("TELEGRAM_CHAT_ID"))
        self.dry, self.sent = dry, []

    def send(self, text: str):
        self.sent.append(text)
        print("\n----- TELEGRAM -----\n" + text + "\n--------------------", flush=True)
        if self.dry or not (self.token and self.chat):
            return
        for _ in range(3):
            try:
                r = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                                  data={"chat_id": self.chat, "text": text, "parse_mode": "HTML",
                                        "disable_web_page_preview": True}, timeout=20)
                print("Telegram:", r.status_code, flush=True)
                if r.status_code == 200:
                    return
            except Exception as exc:  # noqa: BLE001
                print("Telegram fail:", type(exc).__name__)
            time.sleep(3)


# --------------------------------------------------------------------------- market data
class AngelAPI:
    """Thin wrapper; SmartApi logging silenced (it prints credentials on errors)."""

    def __init__(self):
        import logzero
        logzero.loglevel(logging.CRITICAL + 1)
        import pyotp
        from SmartApi import SmartConnect
        e = lambda k: "".join(c for c in os.getenv(k, "").strip() if unicodedata.category(c) != "Cf")  # noqa: E731
        self.api = SmartConnect(api_key=e("ANGEL_API_KEY"))
        try:
            r = self.api.generateSession(e("ANGEL_CLIENT_ID"), e("ANGEL_PASSWORD"), pyotp.TOTP(e("ANGEL_TOTP_SECRET")).now())
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"Angel login failed ({type(exc).__name__})")
        if not r or not r.get("status"):
            raise SystemExit(f"Angel login failed: {(r or {}).get('message')}")
        master = requests.get(SCRIP_MASTER, timeout=90).json()
        self.tok = {str(i["symbol"]).upper()[:-3]: str(i["token"]) for i in master
                    if str(i.get("exch_seg")).upper() == "NSE" and str(i.get("symbol", "")).upper().endswith("-EQ")}
        self.tok["NIFTY"] = NIFTY_TOKEN

    def candles(self, sym: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
        tok = self.tok.get(sym)
        if not tok:
            return pd.DataFrame()
        p = {"exchange": "NSE", "symboltoken": tok, "interval": interval,
             "fromdate": start.strftime("%Y-%m-%d %H:%M"), "todate": end.strftime("%Y-%m-%d %H:%M")}
        for attempt in range(3):
            try:
                r = self.api.getCandleData(p)
                time.sleep(0.36)
                if r and r.get("status"):
                    d = pd.DataFrame(r.get("data") or [], columns=["ts", "open", "high", "low", "close", "volume"])
                    if d.empty:
                        return d
                    d.index = pd.to_datetime(d.pop("ts"), utc=True).dt.tz_convert(IST).dt.tz_localize(None)
                    return d.astype(float)
            except Exception as exc:  # noqa: BLE001
                print("candles retry", sym, type(exc).__name__, flush=True)
            time.sleep(2 + attempt * 3)
        return pd.DataFrame()

    def quotes(self, syms: list[str]) -> dict:
        """{sym: dict(ltp, open, prev_close, vwap, volume)}"""
        want = {self.tok[s]: s for s in syms if s in self.tok}
        out, ids = {}, list(want)
        for i in range(0, len(ids), 50):
            for attempt in range(3):
                try:
                    r = self.api.getMarketData("FULL", {"NSE": ids[i:i + 50]})
                    if r and r.get("status"):
                        for q in (r.get("data") or {}).get("fetched", []):
                            s = want.get(str(q.get("symbolToken")))
                            if s and q.get("ltp"):
                                out[s] = {"ltp": float(q["ltp"]), "open": float(q.get("open") or 0),
                                          "prev_close": float(q.get("close") or 0),
                                          "vwap": float(q.get("avgPrice") or q["ltp"]),
                                          "volume": float(q.get("tradeVolume") or 0)}
                        break
                except Exception as exc:  # noqa: BLE001
                    print("quotes retry", type(exc).__name__, flush=True)
                time.sleep(2)
            time.sleep(1.1)
        return out


def nse_leverage(today: date) -> dict:
    """symbol -> max intraday leverage from today's / latest NSE VaR file (capped 5x)."""
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0", "Referer": "https://www.nseindia.com/"})
    for back in range(0, 7):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        for n in (6, 5, 4, 3, 2, 1):
            try:
                r = s.get(f"https://nsearchives.nseindia.com/archives/nsccl/var/C_VAR1_{d:%d%m%Y}_{n}.DAT", timeout=20)
                if r.status_code == 200 and len(r.text) > 1000:
                    lev = {}
                    for line in r.text.splitlines():
                        p = [x.strip() for x in line.split(",")]
                        if len(p) >= 10 and p[2] in ("EQ", "BE"):
                            try:
                                lev[p[1].upper()] = min(5.0, 100 / float(p[-1]))
                            except (ValueError, ZeroDivisionError):
                                pass
                    print(f"NSE margin file {d} #{n}: {len(lev)} stocks", flush=True)
                    return lev
            except Exception:  # noqa: BLE001
                pass
    return {}


# --------------------------------------------------------------------------- indicators
def prep5(df: pd.DataFrame, ema_span: int) -> pd.DataFrame:
    f = df.copy()
    f["date"] = f.index.normalize()
    f["mins"] = f.index.hour * 60 + f.index.minute
    tp = (f["high"] + f["low"] + f["close"]) / 3
    cv = f.groupby("date")["volume"].cumsum()
    f["vwap"] = ((tp * f["volume"]).groupby(f["date"]).cumsum() / cv.replace(0, np.nan)).fillna(tp)
    f["ema"] = f["close"].ewm(span=ema_span, adjust=False, min_periods=ema_span).mean()
    return f


# --------------------------------------------------------------------------- the bot
class E2Bot:
    def __init__(self, api, clock: Clock, tg: Telegram, cfg: Cfg, state_dir: Path, git_save: bool):
        self.api, self.clock, self.tg, self.cfg, self.dir = api, clock, tg, cfg, state_dir
        self.git = git_save
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_f, self.ledger_f, self.daily_f = self.dir / "state.json", self.dir / "ledger.csv", self.dir / "daily.csv"
        self.state = json.loads(self.state_f.read_text()) if self.state_f.exists() else {
            "start_capital": cfg.capital, "realised": 0.0, "day": None}
        bt.BROKERAGE.update({"pct": 0.10, "cap": 20.0, "min": 5.0})
        self.today = self.clock.now().date()

    # ---------- helpers
    def at(self, hhmm: str, sec: int = 0) -> datetime:
        h, m = map(int, hhmm.split(":"))
        return datetime.combine(self.today, datetime.min.time(), IST).replace(hour=h, minute=m, second=sec)

    def capital(self):
        return self.state["start_capital"] + self.state["realised"]

    def head(self, title: str) -> str:
        return f"<b>{title}</b>\n📘 <b>{STRATEGY}</b> · {VERSION} · PAPER\n📅 {self.today:%d-%b-%Y (%a)}"

    def save(self, msg="state"):
        self.state_f.write_text(json.dumps(self.state, indent=1, default=str))
        if self.git:
            try:
                subprocess.run(["git", "add", str(self.dir)], cwd=ROOT, check=False)
                r = subprocess.run(["git", "commit", "-q", "-m", f"paper E2: {msg} [skip ci]"], cwd=ROOT)
                if r.returncode == 0:
                    for _ in range(3):
                        if subprocess.run(["git", "pull", "-q", "--rebase", "origin", "main"], cwd=ROOT).returncode == 0 and \
                                subprocess.run(["git", "push", "-q"], cwd=ROOT).returncode == 0:
                            break
                        time.sleep(5)
            except Exception as exc:  # noqa: BLE001
                print("git save failed", type(exc).__name__)

    DAILY_COLS = ["date", "nifty_chg", "regime", "side", "status", "reason", "sectors", "candidates", "trades", "net", "capital"]

    def append(self, f: Path, row: dict):
        df = pd.DataFrame([row])
        if f == self.daily_f:
            df = df.reindex(columns=self.DAILY_COLS)
        df.to_csv(f, mode="a", header=not f.exists(), index=False)

    # ---------- pre-market
    def premarket(self):
        c = self.cfg
        read = lambda p: [x.strip().upper() for x in Path(p).read_text().splitlines() if x.strip()]  # noqa: E731
        self.universe = read(c.files["universe"])
        tradable = set(read(c.files["tradable"]))
        sm = pd.read_csv(c.files["sector_map"])
        self.sector = dict(zip(sm["symbol"].str.upper(), sm["sector"]))
        lev = nse_leverage(self.today) if hasattr(self.api, "tok") else {}
        self.lev = lev
        if lev:
            tradable = {s for s in tradable if lev.get(s, 0) >= c.min_leverage}
        self.tradable = tradable
        now = self.clock.now()
        start = datetime.combine(self.today - timedelta(days=45), datetime.min.time())
        end = datetime.combine(self.today, datetime.min.time())
        self.daily = {}
        for i, s in enumerate(self.universe):
            d = self.api.candles(s, "ONE_DAY", start, end)
            if len(d):
                d = d[d.index.normalize() < pd.Timestamp(self.today)]
                if len(d) >= 10:
                    self.daily[s] = {"prev_close": float(d["close"].iloc[-1]),
                                     "turnover": float(((d["close"] * d["volume"]).tail(20) / 1e7).median()),
                                     "avg_vol": float(d["volume"].tail(20).median())}
            if i % 100 == 99:
                print(f"daily {i + 1}/{len(self.universe)}", flush=True)
        # opening-volume baseline (09:15-10:15) of last 10 days for liquid tradable stocks
        self.base_vol = {}
        liquid = [s for s in self.tradable if s in self.daily and self.daily[s]["turnover"] >= c.min_turnover_cr
                  and self.daily[s]["prev_close"] >= c.min_price and self.daily[s]["avg_vol"] >= c.min_avg_vol]
        s0 = datetime.combine(self.today - timedelta(days=16), datetime.min.time())
        for s in liquid:
            d = self.api.candles(s, "FIVE_MINUTE", s0, end)
            if len(d):
                d = d[(d.index.normalize() < pd.Timestamp(self.today))]
                m = d.index.hour * 60 + d.index.minute
                cv = d[m < tmin(c.decision)].groupby(d[m < tmin(c.decision)].index.normalize())["volume"].sum()
                if len(cv) >= 5:
                    self.base_vol[s] = float(cv.tail(10).mean())
        nd = self.api.candles("NIFTY", "ONE_DAY", datetime.combine(self.today - timedelta(days=200), datetime.min.time()), end)
        nd = nd[nd.index.normalize() < pd.Timestamp(self.today)]
        ema = nd["close"].ewm(span=c.regime_ema, adjust=False, min_periods=c.regime_ema).mean()
        self.nifty_prev = float(nd["close"].iloc[-1])
        self.nifty_ema20 = float(ema.iloc[-1])
        self.regime = 1 if self.nifty_prev > self.nifty_ema20 else -1
        print(f"premarket done in {(self.clock.now() - now).seconds}s: universe {len(self.universe)}, "
              f"daily {len(self.daily)}, tradable {len(self.tradable)}, liquid {len(liquid)}, rvol base {len(self.base_vol)}, "
              f"regime {self.regime}", flush=True)

    # ---------- 10:15 decision
    def decide(self) -> list[str]:
        c = self.cfg
        n5 = self.api.candles("NIFTY", "FIVE_MINUTE", self.at("09:15") - timedelta(days=4), self.clock.now())
        nt = n5[n5.index.normalize() == pd.Timestamp(self.today)] if len(n5) else n5
        if nt.empty:
            self.tg.send(self.head("🏖️ Market band / data nahi") + "\n\nAaj NSE ka data nahi mila (chhutti ya API dikkat). Koi trade nahi.")
            return []
        nt = nt[(nt.index.hour * 60 + nt.index.minute) < tmin(c.decision)]
        n_open, n_last = float(nt["open"].iloc[0]), float(nt["close"].iloc[-1])
        nchg = (n_last / self.nifty_prev - 1) * 100
        side = 1 if (nchg >= 0 and n_last > n_open) else (-1 if (nchg <= 0 and n_last < n_open) else 0)
        reg_txt = "Upar ✅ (BUY trend)" if self.regime == 1 else "Neeche ✅ (SHORT trend)"
        nifty_line = (f"📊 Nifty: {n_last:,.1f} ({nchg:+.2f}%) | Open {n_open:,.1f}\n"
                      f"📈 20-din EMA ({self.nifty_ema20:,.0f}): kal close {reg_txt}")
        plan = {"nifty": n_last, "nchg": round(nchg, 2), "regime": self.regime, "side": side}
        self.state["day"] = {"date": str(self.today), "plan": plan, "candidates": [], "positions": [], "done": False}
        if side == 0 or side != self.regime:
            why = ("Nifty 10:15 par confused hai (kal ke close aur aaj ke open ke beech)" if side == 0 else
                   "Aaj Nifty ki direction 20-din trend ke ulta hai")
            self.tg.send(self.head("⏸️ Aaj NO-TRADE din") + f"\n\n{nifty_line}\n\n❌ <b>Wajah:</b> {why}.\n"
                         "Strategy aise din trade nahi karti (backtest me inhi dino loss tha).\n\n" + STRATEGY_DETAIL)
            self.finish_day(no_trade=why)
            return []
        q = self.api.quotes(self.universe)
        rows = []
        for s, v in q.items():
            dd = self.daily.get(s)
            if not dd or not v["prev_close"]:
                continue
            rows.append({"symbol": s, "sector": self.sector.get(s, "UNKNOWN"), "ltp": v["ltp"],
                         "chg": (v["ltp"] / v["prev_close"] - 1) * 100, "vwap": v["vwap"], "vol": v["volume"],
                         "turnover": dd["turnover"], "avg_vol": dd["avg_vol"], "prev_close": dd["prev_close"],
                         "rvol": v["volume"] / self.base_vol[s] if self.base_vol.get(s) else np.nan})
        snap = pd.DataFrame(rows)
        liq = snap[snap["turnover"] >= c.min_turnover_cr]
        sec = liq[liq["sector"] != "UNKNOWN"].groupby("sector")["chg"].agg(["median", "count"])
        sec = sec[sec["count"] >= c.min_sector_stocks]
        sec = (sec[sec["median"] > max(nchg, 0)].sort_values("median", ascending=False) if side == 1 else
               sec[sec["median"] < min(nchg, 0)].sort_values("median"))
        chosen = list(sec.index[:c.top_sectors])
        word = "BUY" if side == 1 else "SHORT"
        if not chosen:
            why = f"Koi sector Nifty se zyada strong nahi ({word} side)"
            self.tg.send(self.head("⏸️ Aaj NO-TRADE din") + f"\n\n{nifty_line}\n➡️ Direction: {word}\n\n❌ <b>Wajah:</b> {why}.")
            self.finish_day(no_trade=why)
            return []
        cand = liq[liq["sector"].isin(chosen) & liq["symbol"].isin(self.tradable)]
        cand = cand[(cand["prev_close"] >= c.min_price) & (cand["avg_vol"] >= c.min_avg_vol) & (cand["rvol"] >= c.min_rvol)]
        if side == 1:
            cand = cand[(cand["chg"] >= c.stock_min) & (cand["chg"] <= c.stock_max) & (cand["ltp"] > cand["vwap"])]
            cand = cand.sort_values("chg", ascending=False)
        else:
            cand = cand[(cand["chg"] <= -c.stock_min) & (cand["chg"] >= -c.stock_max) & (cand["ltp"] < cand["vwap"])]
            cand = cand.sort_values("chg")
        cand = cand.head(c.max_candidates)
        sec_txt = "\n".join(f"   • {s} ({sec.loc[s, 'median']:+.2f}%)" for s in chosen)
        if cand.empty:
            why = "Strong sectors me koi stock shartein (move 1–3.5%, volume 1.5x, VWAP) poori nahi karta"
            self.tg.send(self.head("⏸️ Aaj NO-TRADE din") + f"\n\n{nifty_line}\n➡️ Direction: <b>{word}</b>\n🏭 Strong sectors:\n{sec_txt}\n\n❌ <b>Wajah:</b> {why}.")
            self.finish_day(no_trade=why)
            return []
        lines = [f"{i + 1}. <b>{r.symbol}</b> ({r.sector[:22]}) ₹{r.ltp:,.2f} | {r.chg:+.2f}% | Vol {r.rvol:.1f}x | "
                 f"Lev {self.lev.get(r.symbol, 4):.1f}x" for i, r in enumerate(cand.itertuples())]
        self.state["day"]["plan"].update({"sectors": chosen, "word": word})
        self.state["day"]["candidates"] = list(cand["symbol"])
        self.save("day plan")
        self.tg.send(self.head(f"🧭 DAY PLAN — aaj {'🟢 BUY' if side == 1 else '🔴 SHORT'} din") + f"\n\n{nifty_line}\n"
                     f"➡️ Direction: <b>{word}</b> ✅\n\n🏭 <b>Strong sectors:</b>\n{sec_txt}\n\n🎯 <b>Watchlist (pullback ka wait):</b>\n"
                     + "\n".join(lines) + f"\n\n⏳ Entry window: 10:15 – {c.last_entry} | Max {c.max_trades} trade | Exit {c.exit_time}\n"
                     f"💰 Paper capital: {inr(self.capital())} | Margin/trade {inr(c.margin_per_trade)}\n\n" + STRATEGY_DETAIL)
        return list(cand["symbol"])

    # ---------- entries
    def check_entries(self, cands: list[str], checked: dict):
        c = self.cfg
        day = self.state["day"]
        side = day["plan"]["side"]
        if len(day["positions"]) >= c.max_trades:
            return
        now = self.clock.now()
        s0 = self.at("09:15") - timedelta(days=4)
        n5 = self.api.candles("NIFTY", "FIVE_MINUTE", s0, now)
        if n5.empty:
            return
        nf = prep5(n5, c.ema)
        nf = nf[nf.index + pd.Timedelta(minutes=5) <= pd.Timestamp(now.replace(tzinfo=None))]
        nbar = {int(m): (cl, em) for m, cl, em, d in zip(nf["mins"], nf["close"], nf["ema"], nf["date"])
                if d == pd.Timestamp(self.today)}
        traded = {p["symbol"] for p in day["positions"]}
        for s in cands:
            if s in traded or len(day["positions"]) >= c.max_trades:
                continue
            d = self.api.candles(s, "FIVE_MINUTE", s0, now)
            if d.empty:
                continue
            f = prep5(d, c.ema)
            f = f[f.index + pd.Timedelta(minutes=5) <= pd.Timestamp(now.replace(tzinfo=None))]   # closed bars only
            t = f[f["date"] == pd.Timestamp(self.today)]
            if t.empty:
                continue
            o, h, l, cl, v = (t[x].values for x in ("open", "high", "low", "close", "volume"))
            ema, vw, mins = t["ema"].values, t["vwap"].values, t["mins"].values
            for k in range(len(t)):
                mk = int(mins[k])
                if mk < tmin(c.decision) or mk > tmin(c.last_entry) or checked.get(s, -1) >= mk:
                    continue
                checked[s] = mk
                if np.isnan(ema[k]):
                    continue
                tol = c.touch_tol / 100
                trig = (l[k] <= ema[k] * (1 + tol) and cl[k] > ema[k] and cl[k] > vw[k] and cl[k] > o[k]) if side == 1 else \
                       (h[k] >= ema[k] * (1 - tol) and cl[k] < ema[k] and cl[k] < vw[k] and cl[k] < o[k])
                if not trig:
                    continue
                nb = nbar.get(mk)
                if nb is None or side * (nb[0] - nb[1]) <= 0:
                    continue
                pc = bt._poc((h[:k + 1] + l[:k + 1] + cl[:k + 1]) / 3, v[:k + 1], l[:k + 1].min(), h[:k + 1].max())
                if np.isnan(pc) or side * (cl[k] - pc) <= 0:
                    continue
                stop = l[max(0, k - 2):k + 1].min() * (1 - 0.0005) if side == 1 else h[max(0, k - 2):k + 1].max() * (1 + 0.0005)
                if k != len(t) - 1:
                    continue          # only act on the candle that just closed (older ones = missed)
                ltp = self.api.quotes([s]).get(s, {}).get("ltp") or float(cl[k])
                risk_pct = (ltp - stop) / ltp * 100 * side
                if risk_pct <= 0 or risk_pct > c.sl_max_pct:
                    continue
                self.enter(s, side, ltp, stop, mk, nb, pc, ema[k], vw[k])
                break

    def enter(self, s, side, ltp, stop, mk, nb, pc, ema, vw):
        c = self.cfg
        lev = float(self.lev.get(s, c.min_leverage))
        px = ltp * (1 + side * c.slippage_pct / 100)
        qty = int(c.margin_per_trade * lev / px)
        if qty < 1:
            return
        pos = {"symbol": s, "sector": self.sector.get(s, ""), "side": "LONG" if side == 1 else "SHORT",
               "entry_time": self.clock.now().strftime("%H:%M"), "signal_candle": f"{mk // 60:02d}:{mk % 60:02d}",
               "entry": round(px, 2), "qty": qty, "value": round(px * qty, 0), "lev": round(lev, 2),
               "ref_sl": round(stop, 2)}
        self.state["day"]["positions"].append(pos)
        self.save(f"entry {s}")
        n = len(self.state["day"]["positions"])
        word, icon = ("BUY", "🟢") if side == 1 else ("SHORT SELL", "🔴")
        self.tg.send(self.head(f"{icon} PAPER ENTRY — {word} #{n}") +
                     f"\n\n🏷️ <b>{s}</b> ({pos['sector']})\n💵 Entry: <b>₹{px:,.2f}</b> @ {pos['entry_time']} "
                     f"(signal candle {pos['signal_candle']})\n📦 Qty: {qty} | Position: {inr(pos['value'])} "
                     f"(margin {inr(c.margin_per_trade)} × {lev:.1f}x)\n"
                     f"🛑 Ref low/high (sirf info, SL nahi): ₹{stop:,.2f}\n⏰ Exit: {c.exit_time} automatic\n\n"
                     f"✅ <b>Kyun:</b> EMA20 pullback (EMA ₹{ema:,.2f}) · VWAP ₹{vw:,.2f} ke {'upar' if side == 1 else 'neeche'} · "
                     f"Nifty 5-min EMA20 {'upar' if side == 1 else 'neeche'} ({nb[0]:,.1f} vs {nb[1]:,.1f}) · "
                     f"POC ₹{pc:,.2f} ke {'upar' if side == 1 else 'neeche'}\n"
                     f"📌 Aaj trades: {n}/{c.max_trades}")

    # ---------- live P&L / exit
    def mtm(self, final=False):
        pos = self.state["day"]["positions"] if self.state.get("day") else []
        if not pos:
            return []
        q = self.api.quotes([p["symbol"] for p in pos])
        out = []
        for p in pos:
            side = 1 if p["side"] == "LONG" else -1
            ltp = q.get(p["symbol"], {}).get("ltp") or p["entry"]
            px = ltp * (1 - side * self.cfg.slippage_pct / 100) if final else ltp
            gross = (px - p["entry"]) * p["qty"] * side
            bv, sv = (p["entry"] * p["qty"], px * p["qty"]) if side == 1 else (px * p["qty"], p["entry"] * p["qty"])
            ch = bt.charges(bv, sv)
            out.append((p, ltp, px, gross, ch, gross - ch))
        return out

    def hourly(self):
        r = self.mtm()
        if not r:
            return
        tot = sum(x[5] for x in r)
        lines = [f"{'🟢' if x[5] >= 0 else '🔴'} <b>{x[0]['symbol']}</b> {x[0]['side']} | entry ₹{x[0]['entry']:,.2f} → "
                 f"₹{x[1]:,.2f} | {inr(x[5], True)} ({(x[1] / x[0]['entry'] - 1) * 100 * (1 if x[0]['side'] == 'LONG' else -1):+.2f}%)"
                 for x in r]
        self.tg.send(self.head(f"⏱️ LIVE TRACKING {self.clock.now():%H:%M}") + "\n\n" + "\n".join(lines) +
                     f"\n\n💼 Abhi ka P&L (charges ke baad): <b>{inr(tot, True)}</b>\n⏰ Exit {self.cfg.exit_time}")

    def exit_all(self):
        r = self.mtm(final=True)
        c = self.cfg
        lines, tot = [], 0.0
        for p, ltp, px, gross, ch, net in r:
            tot += net
            row = {"date": str(self.today), "strategy": STRATEGY, "version": VERSION, "symbol": p["symbol"],
                   "sector": p["sector"], "side": p["side"], "signal_candle": p["signal_candle"],
                   "entry_time": p["entry_time"], "entry": p["entry"], "qty": p["qty"], "position_value": p["value"],
                   "margin": c.margin_per_trade, "leverage": p["lev"], "exit_time": self.clock.now().strftime("%H:%M"),
                   "exit": round(px, 2), "gross": round(gross, 2), "charges": round(ch, 2), "net": round(net, 2),
                   "net_pct_margin": round(net / c.margin_per_trade * 100, 2), "reason": "TIME_15:00"}
            self.append(self.ledger_f, row)
            move = (px / p["entry"] - 1) * 100 * (1 if p["side"] == "LONG" else -1)
            lines.append(f"{'✅' if net > 0 else '❌'} <b>{p['symbol']}</b> {p['side']}\n"
                         f"   ₹{p['entry']:,.2f} → ₹{px:,.2f} ({move:+.2f}%) × {p['qty']}\n"
                         f"   Gross {inr(gross, True)} | Charges ₹{ch:,.0f} | <b>Net {inr(net, True)}</b> "
                         f"({net / c.margin_per_trade * 100:+.1f}% margin par)")
        self.state["realised"] += tot
        return lines, tot

    def finish_day(self, no_trade: str | None = None):
        c = self.cfg
        lines, tot = ([], 0.0) if no_trade else self.exit_all()
        day = self.state.get("day") or {"plan": {}}
        led = pd.read_csv(self.ledger_f) if self.ledger_f.exists() else pd.DataFrame(columns=["date", "net"])
        led["date"] = pd.to_datetime(led["date"])
        month = led[led["date"].dt.to_period("M") == pd.Timestamp(self.today).to_period("M")]
        wins = int((led["net"] > 0).sum()) if len(led) else 0
        self.append(self.daily_f, {"date": str(self.today), "nifty_chg": day["plan"].get("nchg"), "regime": day["plan"].get("regime"),
                                   "side": day["plan"].get("side"), "status": "NO_TRADE" if no_trade else ("TRADED" if lines else "NO_ENTRY"),
                                   "reason": no_trade or "", "sectors": "|".join(day["plan"].get("sectors", [])),
                                   "candidates": "|".join(day.get("candidates", [])), "trades": len(lines),
                                   "net": round(tot, 2), "capital": round(self.capital(), 2)})
        if day.get("date"):
            day["done"] = True
        self.save("day end")
        if no_trade:
            return
        cap = self.capital()
        body = "\n".join(lines) if lines else "Aaj watchlist bani, lekin 13:30 tak koi pullback entry nahi mili."
        self.tg.send(self.head("🏁 DAY CLOSE — 3:00 PM exit") + f"\n\n{body}\n\n"
                     f"📌 <b>Aaj ka net: {inr(tot, True)}</b> ({tot / c.capital * 100:+.2f}% capital)\n"
                     f"🗓️ Is mahine: {inr(month['net'].sum(), True)} ({len(month)} trades)\n"
                     f"📒 Total paper: {inr(self.state['realised'], True)} | {len(led)} trades | jeet {wins}/{len(led)}\n"
                     f"💰 Paper capital: <b>{inr(cap)}</b> (shuru {inr(self.state['start_capital'])})")

    # ---------- main
    def run(self):
        c = self.cfg
        now = self.clock.now()
        if now.weekday() >= 5:
            print("weekend")
            return
        d = self.state.get("day")
        if d and d.get("date") == str(self.today) and d.get("done"):
            print("aaj ka session ho chuka")
            return
        if now.weekday() == 0:
            if not (d and d.get("date") == str(self.today)):
                self.state["day"] = {"date": str(self.today), "plan": {}, "candidates": [], "positions": [], "done": True}
                self.append(self.daily_f, {"date": str(self.today), "status": "MONDAY_OFF", "trades": 0, "net": 0,
                                           "capital": round(self.capital(), 2)})
                self.save("monday")
                self.tg.send(self.head("😴 Somvaar — strategy OFF") + "\n\nBacktest me Somvaar ko dono saal loss tha, "
                             "isliye E2 Somvaar ko trade nahi karti. Kal 10:15 par milte hain.\n\n" + STRATEGY_DETAIL)
            return
        if now > self.at(c.exit_time):
            print("too late for today")
            return
        resume = d and d.get("date") == str(self.today)
        self.premarket()
        if resume and d.get("candidates"):
            cands = d["candidates"]
            self.tg.send(self.head("♻️ Bot dobara shuru (resume)") + f"\n\nWatchlist: {', '.join(cands)} | "
                         f"Khule trades: {len(d['positions'])}")
        else:
            self.clock.sleep_until(self.at(c.decision, 20))
            cands = self.decide()
            if not cands:
                return
        checked: dict = {}
        next_hour = {11: False, 12: False, 13: False, 14: False}
        t = self.at(c.decision, 20)
        while True:
            t += timedelta(minutes=5)
            if t > self.at(c.exit_time):
                break
            self.clock.sleep_until(t)
            now = self.clock.now()
            if now <= self.at(c.last_entry) + timedelta(minutes=6) and len(self.state["day"]["positions"]) < c.max_trades:
                try:
                    self.check_entries(cands, checked)
                except Exception as exc:  # noqa: BLE001
                    print("check_entries error:", type(exc).__name__, exc, flush=True)
            if now.minute < 5 and now.hour in next_hour and not next_hour[now.hour]:
                next_hour[now.hour] = True
                self.hourly()
            if now > self.at(c.last_entry) + timedelta(minutes=6) and not self.state["day"]["positions"]:
                break
        self.clock.sleep_until(self.at(c.exit_time, 5))
        self.finish_day()


def intro(tg: Telegram):
    tg.send(f"<b>🚀 PAPER TRADING SHURU</b>\n📘 <b>{STRATEGY}</b> · {VERSION}\n\n" + STRATEGY_DETAIL +
            "\n\n<b>Aapko kab-kab message aayega:</b>\n"
            "🧭 10:15 — DAY PLAN (aaj BUY/SHORT/no-trade, sectors, watchlist) ya NO-TRADE ki wajah\n"
            "🟢/🔴 Entry hote hi — stock, price, qty, leverage, entry ki wajah\n"
            "⏱️ 11, 12, 1, 2 baje — khule trades ka live P&amp;L\n"
            "🏁 3:00 PM — exit, charges, net P&amp;L, mahine aur total ka hisaab\n"
            "😴 Somvaar — strategy off ka message\n\n"
            "💰 Paper capital ₹1,00,000 · ₹10,000 margin × leverage (4–5x) per trade · brokerage ₹20/order + sab charges\n"
            "📒 Har trade repo me: paper_e2/ledger.csv\n\n⚠️ Yeh PAPER trading hai — koi asli order nahi lagta.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["live", "intro", "sample"], default="live")
    ap.add_argument("--dry", action="store_true", help="do not send Telegram")
    args = ap.parse_args()
    tg = Telegram(dry=args.dry)
    if args.mode == "intro":
        intro(tg)
        return
    if args.mode == "sample":
        for m in json.loads((HERE / "sample_messages.json").read_text()):
            tg.send("🧪 <b>SAMPLE — nakli data, sirf format dikhane ke liye</b>\n\n" + m)
            time.sleep(1)
        return
    bot = E2Bot(AngelAPI(), Clock(), tg, Cfg(), HERE / "state", git_save=bool(os.getenv("GITHUB_ACTIONS")))
    bot.run()


if __name__ == "__main__":
    main()
