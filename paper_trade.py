#!/usr/bin/env python3
"""
SHORT paper trading for the NSE SMC scanner.  NO real orders - virtual trades only.

* Every SHORT signal the scanner alerts is recorded as a virtual trade
  (entry = signal candle close, SL / TP from the scanner settings, Rs 1 lakh).
* Exits use the SAME rule as the 180-day backtest, on closed 5-minute candles:
  SL checked first in a candle, then TP, gap through a level fills at the open,
  otherwise squared off at the close of the 15:15 IST candle.
* The ledger lives in the repo branch `paper-trades` (file paper/trades.csv) so it
  survives the fresh process GitHub starts every 15 minutes.
  Without GITHUB_TOKEN it uses a local file instead (PAPER_LEDGER_FILE).
"""
import base64
import io
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

IST = ZoneInfo("Asia/Kolkata")
CAPITAL_PER_TRADE = float(os.getenv("PAPER_CAPITAL_PER_TRADE", "100000"))
COST_PCT = float(os.getenv("PAPER_COST_PCT", "0.10"))      # round-trip charges deducted
EOD_EXIT_MINUTE = 15 * 60 + 15                               # exit at close of 15:15 candle
LEDGER_BRANCH = os.getenv("PAPER_LEDGER_BRANCH", "paper-trades")
LEDGER_PATH = "paper/trades.csv"
COLUMNS = ["id", "date", "symbol", "token", "side", "signal_candle", "entry", "sl", "tp",
           "status", "exit_time", "exit", "gross_pct", "net_pct", "net_rs", "zone"]


# ------------------------------------------------------------------ ledger storage
class Ledger:
    def __init__(self):
        self.token = os.getenv("GITHUB_TOKEN", "").strip()
        self.repo = os.getenv("GITHUB_REPOSITORY", "").strip()
        self.local = os.getenv("PAPER_LEDGER_FILE", "data/paper/trades.csv")
        self.remote = bool(self.token and self.repo)
        self.sha = None

    # --- GitHub contents API helpers ---
    def _h(self):
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"}

    def _api(self, path):
        return f"https://api.github.com/repos/{self.repo}/{path}"

    def _ensure_branch(self):
        r = requests.get(self._api(f"git/ref/heads/{LEDGER_BRANCH}"), headers=self._h(), timeout=20)
        if r.status_code == 200:
            return
        repo = requests.get(self._api(""), headers=self._h(), timeout=20).json()
        base = requests.get(self._api(f"git/ref/heads/{repo.get('default_branch', 'main')}"),
                            headers=self._h(), timeout=20).json()["object"]["sha"]
        requests.post(self._api("git/refs"), headers=self._h(), timeout=20,
                      json={"ref": f"refs/heads/{LEDGER_BRANCH}", "sha": base})

    def load(self):
        if not self.remote:
            if os.path.exists(self.local):
                return _as_text(pd.read_csv(self.local, dtype=str, keep_default_na=False))
            return _as_text(pd.DataFrame(columns=COLUMNS))
        r = requests.get(self._api(f"contents/{LEDGER_PATH}"), params={"ref": LEDGER_BRANCH},
                         headers=self._h(), timeout=20)
        if r.status_code == 404:
            self.sha = None
            return _as_text(pd.DataFrame(columns=COLUMNS))
        r.raise_for_status()
        j = r.json()
        self.sha = j["sha"]
        text = base64.b64decode(j["content"]).decode("utf-8")
        return _as_text(pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False))

    def save(self, df, message):
        df = df.reindex(columns=COLUMNS)
        if not self.remote:
            os.makedirs(os.path.dirname(self.local) or ".", exist_ok=True)
            df.to_csv(self.local, index=False)
            return df
        self._ensure_branch()
        for attempt in range(4):
            body = {"message": message, "branch": LEDGER_BRANCH,
                    "content": base64.b64encode(df.to_csv(index=False).encode()).decode()}
            if self.sha:
                body["sha"] = self.sha
            r = requests.put(self._api(f"contents/{LEDGER_PATH}"), headers=self._h(), json=body, timeout=20)
            if r.status_code in (200, 201):
                self.sha = r.json()["content"]["sha"]
                return df
            if r.status_code in (409, 422):           # someone else wrote first -> merge and retry
                df = merge_ledgers(self.load(), df)
                time.sleep(1 + attempt)
                continue
            r.raise_for_status()
        raise RuntimeError("paper ledger save failed after retries")


def _as_text(df):
    """Ledger is kept as plain text (object dtype) so any value can be written back."""
    return df.reindex(columns=COLUMNS).astype(object).where(lambda x: x.notna(), "")


def merge_ledgers(a, b):
    """Union by id; a CLOSED row always wins over an OPEN row of the same id."""
    both = pd.concat([a, b], ignore_index=True)
    both["_closed"] = ~both["status"].fillna("").isin(["", "OPEN"])
    both = both.sort_values("_closed").drop_duplicates("id", keep="last").drop(columns="_closed")
    return both.sort_values("signal_candle").reset_index(drop=True)


# ------------------------------------------------------------------ exit rule (same as backtest)
def evaluate_exit(trade, candles, now=None):
    """trade: dict-like with side, signal_candle, entry, sl, tp.
    candles: DataFrame time/open/high/low/close of CLOSED 5m candles.
    Returns (status, exit_time_str, exit_price) or None if still open."""
    now = now or datetime.now(IST)
    sig = pd.Timestamp(str(trade["signal_candle"])[:19]).tz_localize(IST)
    entry, sl, tp = float(trade["entry"]), float(trade["sl"]), float(trade["tp"])
    short = trade["side"] == "SHORT"
    c = candles.copy()
    c["time"] = pd.to_datetime(c["time"], utc=True).dt.tz_convert(IST)
    c = c[(c["time"] > sig) & (c["time"].dt.date == sig.date())].sort_values("time")
    prev = None
    for _, row in c.iterrows():
        t, o, h, l, cl = row["time"], float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        stamp = t.strftime("%Y-%m-%d %H:%M")
        # 15:15 candle missing ho aur seedha 15:20+ (closing auction) candle aaye to us par SL/TP mat lagao:
        # trade 15:15 pe pichhli candle ke close pe band maano (backtest rule).
        if t.hour * 60 + t.minute > EOD_EXIT_MINUTE:
            if prev is not None:
                return "EOD", prev[0], prev[1]
            return "EOD", stamp, o
        if short:
            if h >= sl:
                return "SL", stamp, max(sl, o)
            if l <= tp:
                return "TP", stamp, min(tp, o)
        else:
            if l <= sl:
                return "SL", stamp, min(sl, o)
            if h >= tp:
                return "TP", stamp, max(tp, o)
        if t.hour * 60 + t.minute >= EOD_EXIT_MINUTE:
            return "EOD", stamp, cl
        prev = (stamp, cl)
    # Day is over but the 15:15 candle is missing -> close at the last candle we have.
    day_end = datetime.combine(sig.date(), datetime.min.time(), IST) + timedelta(hours=15, minutes=20)
    if now >= day_end + timedelta(minutes=5):
        if len(c):
            last = c.iloc[-1]
            return "EOD", last["time"].strftime("%Y-%m-%d %H:%M"), float(last["close"])
        return "EOD", sig.strftime("%Y-%m-%d %H:%M"), entry
    return None


def pnl(side, entry, exit_price):
    gross = (exit_price / entry - 1) * 100 * (-1 if side == "SHORT" else 1)
    net = gross - COST_PCT
    return round(gross, 3), round(net, 3), round(net / 100 * CAPITAL_PER_TRADE, 2)


# ------------------------------------------------------------------ messages
def exit_message(t):
    emoji = {"TP": "🎯", "SL": "🛑", "EOD": "⏰"}[t["status"]]
    label = {"TP": "TARGET HIT", "SL": "STOP LOSS HIT", "EOD": "SQUARED OFF 15:15"}[t["status"]]
    net_rs = float(t["net_rs"])
    return (
        f"📝 <b>PAPER TRADE — {emoji} {label}</b>\n\n"
        f"<b>Instrument:</b> {str(t['symbol']).replace('-EQ', '')}\n"
        f"<b>Side:</b> {t['side']}\n"
        f"<b>Entry:</b> ₹{float(t['entry']):.2f} ({str(t['signal_candle'])[11:16]})\n"
        f"<b>Exit:</b> ₹{float(t['exit']):.2f} ({str(t['exit_time'])[11:16]})\n"
        f"<b>P&L:</b> {float(t['gross_pct']):+.2f}% gross | {float(t['net_pct']):+.2f}% net\n"
        f"<b>P&L on ₹{CAPITAL_PER_TRADE:,.0f}:</b> {'+' if net_rs >= 0 else '-'}₹{abs(net_rs):,.0f}\n\n"
        "Virtual trade only — no real order."
    )


def summary_message(df, day):
    closed = df[~df["status"].fillna("").isin(["", "OPEN"])].copy()
    closed["net_rs"] = closed["net_rs"].astype(float)
    closed["date"] = closed["date"].astype(str)
    today = closed[closed["date"] == str(day)]
    def block(x):
        if x.empty:
            return "No closed trades."
        w = (x["net_rs"] > 0).sum()
        return (f"Trades: {len(x)} | Win: {w} | Loss: {len(x) - w} | Win rate: {w / len(x) * 100:.0f}%\n"
                f"TP: {(x['status'] == 'TP').sum()} | SL: {(x['status'] == 'SL').sum()} | 15:15 exit: {(x['status'] == 'EOD').sum()}\n"
                f"Net P&L: {'+' if x['net_rs'].sum() >= 0 else '-'}₹{abs(x['net_rs'].sum()):,.0f}")
    lines = ["📝 <b>PAPER TRADING — DAILY SUMMARY (SHORT)</b>", f"📅 {day.strftime('%d-%m-%Y')}", "",
             "<b>Today</b>", block(today), "",
             f"<b>Since start ({closed['date'].min() if len(closed) else '-'})</b>", block(closed)]
    if len(today):
        lines += ["", "<b>Today's trades</b>"]
        for _, r in today.iterrows():
            lines.append(f"{str(r['symbol']).replace('-EQ', '')}: {r['status']} {float(r['net_pct']):+.2f}% "
                         f"({'+' if r['net_rs'] >= 0 else '-'}₹{abs(r['net_rs']):,.0f})")
    lines += ["", f"₹{CAPITAL_PER_TRADE:,.0f} per trade, {COST_PCT:.2f}% charges deducted. Virtual only."]
    return "\n".join(lines)


# ------------------------------------------------------------------ public API used by the scanner
class PaperBook:
    def __init__(self, send=None):
        self.ledger = Ledger()
        self.send = send or (lambda msg: True)

    def open_trade(self, result, stock):
        """Record a SHORT signal as a virtual trade. Idempotent by setup id."""
        tid = f"{result['symbol']}|{result['candle_time']}"
        df = self.ledger.load()
        if tid in set(df["id"].astype(str)):
            return False
        row = {"id": tid, "date": result["candle_time"][:10], "symbol": result["symbol"],
               "token": str(stock.get("token", "")), "side": result["signal"],
               "signal_candle": result["candle_time"][:19], "entry": f"{float(result['entry']):.2f}",
               "sl": f"{float(result['sl']):.2f}", "tp": f"{float(result['tp']):.2f}",
               "status": "OPEN", "zone": str(result.get("zone_type", ""))}
        df = _as_text(pd.concat([df, _as_text(pd.DataFrame([row]))], ignore_index=True))
        self.ledger.save(df, f"paper: open {result['signal']} {result['symbol']} {result['candle_time'][:16]}")
        print(f"PAPER TRADE OPENED: {result['symbol']} {result['signal']} @ {float(result['entry']):.2f}")
        return True

    def check_exits(self, get_candles, now=None):
        """get_candles(trade_row) -> DataFrame of closed 5m candles for that trade's day (or None).
        Closes finished trades, sends one Telegram message per closed trade."""
        df = self.ledger.load()
        if df.empty:
            return 0
        open_idx = df.index[df["status"].isin(["", "OPEN"])]
        changed = []
        for i in open_idx:
            t = df.loc[i]
            try:
                candles = get_candles(t)
            except Exception as e:
                print(f"PAPER: candles error {t['symbol']}: {e}")
                continue
            if candles is None or len(candles) == 0:
                candles = pd.DataFrame(columns=["time", "open", "high", "low", "close"])
            res = evaluate_exit(t, candles, now)
            if res is None:
                continue
            status, when, price = res
            g, n, rs = pnl(t["side"], float(t["entry"]), price)
            df.loc[i, ["status", "exit_time", "exit", "gross_pct", "net_pct", "net_rs"]] = \
                [status, when, f"{price:.2f}", f"{g:.3f}", f"{n:.3f}", f"{rs:.2f}"]
            changed.append(i)
        if changed:
            ids = [df.loc[i, "id"] for i in changed]
            df = self.ledger.save(df, f"paper: close {len(changed)} trade(s)")
            for tid in ids:
                row = df[df["id"] == tid].iloc[0]
                self.send(exit_message(row))
                print(f"PAPER TRADE CLOSED: {row['symbol']} {row['status']} net {row['net_pct']}%")
        return len(changed)
