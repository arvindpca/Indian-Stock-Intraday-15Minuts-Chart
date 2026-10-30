"""E2 bot ka poora trading din NAKLI data par chalao (koi Angel/Telegram/git nahi).

Kaam: Monday se pehle crash pakadna.  Asli ghadi ki jagah nakli ghadi hai jo sleep par
seedha aage kood jaati hai, isliye 9:30-15:05 ka din kuch second me chal jata hai.

    python paper_e2/replay_test.py            # kai alag din/scenario
"""
from __future__ import annotations

import sys
import tempfile
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2_live as E  # noqa: E402

IST = E.IST


class FakeClock:
    def __init__(self, start: datetime):
        self.t = start

    def now(self) -> datetime:
        return self.t

    def sleep_until(self, t: datetime):
        if t > self.t:
            self.t = t


class FakeAPI:
    """Har stock ka random-walk data.  trend: +1 sab upar, -1 sab neeche (signal paths chalane ke liye)."""

    def __init__(self, clock: FakeClock, trend: int, seed: int, empty_quotes=False, flaky=False):
        self.clock, self.trend, self.seed = clock, trend, seed
        self.empty_quotes, self.flaky = empty_quotes, flaky
        self.calls = 0

    def _rng(self, sym):
        return np.random.default_rng(abs(hash((sym, self.seed))) % (2 ** 32))

    def _base(self, sym):
        return 100 + (abs(hash(sym)) % 2000)

    _cache: dict = {}

    def _year(self, sym):
        """Poore saal ka daily data (ek baar banao, phir slice)."""
        k = (sym, self.seed, self.trend)
        if k not in self._cache:
            days = pd.bdate_range("2026-01-01", "2026-12-31")
            r = self._rng(sym)
            if sym == "NIFTY":
                px = 24000 * np.cumprod(1 + r.normal(0.0015 * self.trend, 0.004, len(days)))
            else:
                px = self._base(sym) * np.cumprod(1 + r.normal(0.001 * self.trend, 0.01, len(days)))
            self._cache[k] = pd.DataFrame({"open": px, "high": px * 1.01, "low": px * 0.99, "close": px,
                                           "volume": r.integers(300_000, 3_000_000, len(days)).astype(float)},
                                          index=pd.DatetimeIndex(days))
        return self._cache[k]

    def _daily(self, sym, start, end):
        y = self._year(sym)
        return y[(y.index >= pd.Timestamp(start.date())) & (y.index < pd.Timestamp(end.date()))]

    def _prev_close(self, sym, d):
        y = self._year(sym)
        return float(y[y.index < pd.Timestamp(d)]["close"].iloc[-1])

    def _five(self, sym, start, end):
        frames = []
        endn = pd.Timestamp(end.replace(tzinfo=None))
        for d in pd.bdate_range(start.date(), end.date()):
            r = np.random.default_rng(abs(hash((sym, str(d.date()), self.seed))) % (2 ** 32))
            mins = [m for m in range(9 * 60 + 15, 15 * 60 + 30, 5)
                    if pd.Timestamp(d) + pd.Timedelta(minutes=m + 5) <= endn]
            if not mins:
                continue
            n = len(mins)
            steps = r.normal(0.0006 * self.trend, 0.0012, n)
            steps[3::6] -= 0.0035 * self.trend          # EMA ki taraf pullback
            pc = self._prev_close(sym, d)
            c = pc * (1 + 0.002 * self.trend) * np.cumprod(1 + steps)
            o = np.r_[pc * (1 + 0.002 * self.trend), c[:-1]]
            frames.append(pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.0008,
                                        "low": np.minimum(o, c) * 0.9992, "close": c,
                                        "volume": r.integers(20_000, 90_000, n).astype(float)},
                                       index=pd.DatetimeIndex([pd.Timestamp(d) + pd.Timedelta(minutes=m) for m in mins])))
        return pd.concat(frames) if frames else pd.DataFrame()

    def candles(self, sym, interval, start, end):
        self.calls += 1
        if self.flaky and self.calls % 9 == 0:
            return pd.DataFrame()
        if interval == "ONE_DAY":
            return self._daily(sym, start, end)
        return self._five(sym, start, end)

    def quotes(self, syms):
        if self.empty_quotes:
            return {}
        out = {}
        now = self.clock.now()
        for s in syms:
            d = self._five(s, now - timedelta(days=1), now)
            if d.empty:
                continue
            today = d[d.index.normalize() == pd.Timestamp(now.date())]
            pc = self._prev_close(s, now.date())
            ltp = pc * (1 + 0.02 * self.trend * (0.6 + (abs(hash(s)) % 7) / 10))
            out[s] = {"ltp": ltp, "open": pc, "prev_close": pc, "vwap": pc * (1 + 0.01 * self.trend),
                      "volume": 5_000_000.0}
        return out


def run_day(day: date, trend: int, seed: int, start="09:30", state_dir=None, **kw):
    clock = FakeClock(datetime.combine(day, datetime.min.time(), IST).replace(
        hour=int(start[:2]), minute=int(start[3:])))
    tg = E.Telegram(dry=True)
    sd = Path(state_dir or tempfile.mkdtemp())
    bot = E.E2Bot(FakeAPI(clock, trend, seed, **kw), clock, tg, E.Cfg(), sd, git_save=False)
    bot.run()
    return bot, tg


def main() -> int:
    import io
    import contextlib
    scenarios = [
        ("Fri trend UP", date(2026, 10, 30), 1, 1, {}),
        ("Tue trend DOWN", date(2026, 11, 3), -1, 2, {}),
        ("Wed flat/confused", date(2026, 11, 4), 0, 3, {}),
        ("Thu quotes empty (API dikkat)", date(2026, 11, 5), 1, 4, {"empty_quotes": True}),
        ("Fri flaky candles", date(2026, 11, 6), 1, 5, {"flaky": True}),
        ("Mon (strategy off)", date(2026, 11, 2), 1, 6, {}),
    ]
    bad = 0
    shared = tempfile.mkdtemp()   # ledger ek hi folder me -> din-ba-din jodna bhi test hota hai
    for name, day, trend, seed, kw in scenarios:
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                bot, tg = run_day(day, trend, seed, state_dir=shared, **kw)
            titles = [m.split("\n")[0][:60] for m in tg.sent]
            print(f"OK   {name}: {len(tg.sent)} msgs {titles}")
        except BaseException as exc:  # noqa: BLE001
            bad += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=6)
    # resume: bot beech din me restart ho
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            sd = tempfile.mkdtemp()
            run_day(date(2026, 11, 10), 1, 7, state_dir=sd)
            st = E.json.loads((Path(sd) / "state.json").read_text())
            st["day"]["done"] = False
            (Path(sd) / "state.json").write_text(E.json.dumps(st))
            bot, tg = run_day(date(2026, 11, 10), 1, 7, start="11:00", state_dir=sd)
        print(f"OK   resume mid-day: {len(tg.sent)} msgs")
    except BaseException as exc:  # noqa: BLE001
        bad += 1
        print(f"FAIL resume mid-day: {type(exc).__name__}: {exc}")
        traceback.print_exc(limit=6)
    print("RESULT:", "SAB THEEK" if not bad else f"{bad} FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
