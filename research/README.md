# NSE Intraday Strategy Research System

Reproducible discovery + validation of intraday (5-min) LONG and SHORT rules on the
174 stocks in `stock_names.txt`, with Angel One intraday charges, conservative
execution and an untouched out-of-sample period.

> **Koi bhi result live trading ka signal nahi hai.** Jo strategy pass ho, usko pehle
> paper trading me kam se kam 1-2 mahine chalana zaroori hai.

## Kaise chalayein

### GitHub par (asli run, 174 stocks)
Actions → **NSE Intraday Research Pipeline (174 stocks)** → *Run workflow*

| input | matlab |
|---|---|
| `max_symbols` | `0` = sab stocks; `10` = jaldi test |
| `download_budget_min` | itne minute baad download ruk jayega; agla run wahin se continue karega |

Ye workflow Angel One secrets (`ANGEL_API_KEY`, `ANGEL_CLIENT_ID`, `ANGEL_PASSWORD`,
`ANGEL_TOTP_SECRET`) use karta hai — wahi jo repo me pehle se set hain. Data GitHub
cache me rehta hai (dobara download nahi hota). Results `research_results/run_<date>/`
me commit hote hain + artifact me bhi milte hain.

### Local (apne computer par)
```bash
pip install -r requirements.txt -r research/requirements.txt
python -m unittest discover -s research/tests -t . -v          # 16 tests
python research/data_download.py --max-symbols 10              # .env me Angel credentials chahiye
python research/run_research.py                                # pura universe
python research/run_research.py --symbols RELIANCE --min-trades-dev 60 --min-trades-val 25   # sirf demo
```

## Pipeline (sab steps order me)
1. **Data** – `data_download.py`: Angel scrip master se token mapping (missing symbol report hota hai,
   replace nahi), 30-din chunks, resumable cache. RELIANCE/NIFTY repo files se seed.
2. **Quality** – `data_quality.py`: duplicates, invalid OHLC, out-of-session bars, missing bars
   (fill nahi kiye jaate), special sessions, corporate-action jaise gaps, liquidity (median turnover),
   market-calendar missing days → `data_quality.csv`. 15m candles 5m se banti hain aur RELIANCE ki
   alag download hui 15m file se match ki jaati hain.
3. **Features** – `features.py`: 15 triggers (entry events) + 25 filters (conditions), long/short alag.
   Triggers: OPEN (no-filter baseline), NBAR_BRK (price-action baseline), ORB, VWAP_X, EMA_X, MACD_X,
   ST_FLIP (Supertrend), BB_BRK, RSI_REV, PDH_BRK (prev day high/low), BOS, SWEEP (liquidity sweep),
   MSS, FVG, RETEST. Filters: EMA50, EMA_STACK, VWAP, ADX20, DI, RSI_MOM, MACD_POS, ST, BB_IN, RVOL15,
   VOL_SPIKE, BODY, CLOC, ABOVE_OR, GAP_SMALL/WITH/AGAINST, time-of-day (T_AM/T_MID/T_PM), volatility
   regime (VOLREG_HI/LO), daily trend (DTREND), ATR_OK, market regime (MKT = NIFTY vs EMA50).
   Order blocks implement **nahi** kiye (koi objective definition tay nahi thi).
4. **Discovery (sirf DEV period)** – `discovery.py`: stage 1 har trigger × 9 exits; stage 2 top-6
   triggers × har filter; stage 3 top-10 pairs × doosra filter. Har experiment `experiments_dev.csv`
   me. Multiple-testing ke liye Benjamini-Hochberg FDR.
5. **Validation** – dev ke best candidates VAL period par ek baar: trades, expectancy > 0, PF > 1.05,
   ≥55% positive months. → `validation.csv` (reject reasons ke saath).
6. **Walk-forward** – 6 mahine train → 2 mahine test, rolling. Train me kuch profitable nahi → flat.
7. **OOS (untouched)** – sirf accepted strategies, portfolio limits (max 5 positions, ₹3000 daily loss
   limit), cost sensitivity (slippage 0×/1×/2×/3×), breakdown by month/symbol/direction/vol-regime/time.

## Execution & cost assumptions (`config.yaml` me badal sakte hain)
* Signal = complete candle; entry = **agle candle ka open**. Entry window 09:30–14:40, square-off **15:00**
  (NSE Closing Auction Session 3 Aug 2026 se F&O stocks me continuous trading 15:15 par band; broker square-off 15:00).
  15:15 ke baad ke zero-volume auction bars hata diye jaate hain (count `data_quality.csv` me).
* Stop = k×ATR(14); target = rr × risk (rr=0 → sirf SL ya square-off).
* Ek hi candle me SL aur TP dono → **SL maana jaata hai**. Gap se SL ke paar → us candle ka open.
* Ek stock me ek time par ek hi trade.
* Size: ₹1000 risk/trade, max ₹2 lakh notional (leverage assume nahi), max 2% of avg 5-min volume.
* Angel One intraday: brokerage min(₹20, 0.1%) min ₹5 per order, STT 0.025% sell, NSE 0.0030699%,
  SEBI ₹10/cr, stamp 0.003% buy, GST 18%, + slippage 0.03% har fill.

## Output files (`research_results/<run>/`)
`data_quality.csv`, `download_report.csv`, `universe_summary.json`, `experiments_dev.csv`,
`ranked_dev_top10.csv`, `validation.csv`, `walkforward_folds.csv`, `walkforward_trades.csv`,
`walkforward_summary.json`, `baselines.csv`, `oos_results.csv`, `oos_trades.csv`,
`portfolio_oos_trades.csv`, `equity_drawdown_oos.csv`, `oos_breakdown_by_*.csv`, `summary.json`,
`run.log`, `config_used.yaml`, `test_report.txt`.

## Known limitations
* Universe = aaj ki list → survivorship bias (delisted stocks nahi hain).
* Sector map repo me nahi → sector breakdown nahi.
* F&O contract data nahi → options backtest **nahi** kiya (stock OHLCV option data nahi hai).
* Daily loss limit sirf band ho chuke trades ka loss ginta hai.
* Walk-forward pool sirf single-trigger grid hai (filters wali combos discovery/validation me test hoti hain).
