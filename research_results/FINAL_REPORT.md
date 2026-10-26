# NSE Intraday Research – Final Report (run_2026-10-27_0324)

## Verdict
**Koi robust profitable strategy nahi mili.** 169 stocks × 2 saal (28 Oct 2024 – 26 Oct 2026) ke 5-min data par
1016 experiments (15 triggers × 25 filters × 9 exits, long + short) me se **ek bhi** DEV period me
Angel One charges + slippage ke baad positive nahi tha. Isliye validation, walk-forward aur OOS me koi strategy
aage nahi gayi. Live trading ke liye kuch recommend nahi hai.

## Kyun? (measured, hypothesis nahi)
| | value |
|---|---|
| DEV experiments costs se **pehle** positive | 736 / 1016 |
| DEV experiments costs ke **baad** positive | 0 / 1016 |
| Best expectancy, costs se pehle | +0.18 R |
| Best expectancy, costs ke baad | −0.06 R |
| Median cost per trade | 0.25 R (charges + 0.03% slippage har fill) |

Matlab chhota raw edge hai (jaise ORB long, gap ke ulte direction me long), lekin 5-min ATR stop itna chhota hota hai
ki har trade ka fixed kharcha (STT, brokerage, slippage) us edge se bada ho jata hai.

Baselines (no-filter OPEN aur 12-candle breakout) teeno periods (dev/val/oos) me −0.15R se −0.34R per trade.
Walk-forward: 6 folds, har fold me training window me koi config positive nahi → system flat raha (0 trades).

## Data coverage
* 174 requested → 169 usable. NO_TOKEN (Angel master me nahi): STLTECH, ICICIPRULI, HFCL. Kam history: CLEANMAX (160 din), VAML (92 din).
* 98 stocks WARN: zyada-tar F&O stocks me 3 Aug 2026 ke baad 15:15/15:20 candles nahi hain (NSE Closing Auction Session).
  Isliye square-off 15:00 rakha gaya.
* 26 stock-days par >15% overnight gap (jaise 7 Apr 2025, 21 Nov 2024) – ye asli news gaps hain, us din trade skip.
* RELIANCE ka 2024 bonus data me adjusted hai. Sector map nahi, F&O option data nahi.

## Tests
16/16 pass (`test_report.txt`): next-bar entry, same-candle SL+TP → SL, gap through SL, square-off exit,
entry window, no overlap, portfolio max positions + daily loss limit, Angel charges hand-calculation, min brokerage,
metrics, BH-FDR, data cleaning, 15m resample, lookahead truncation test (deliberate leak se fail hota hai – verified).

## Agla research step (hypothesis – abhi test nahi hua)
Costs ka bojh kam karne ke liye bade move wale setups chahiye: 15-min / 1-hour ATR stop, kam lekin bade trades,
ya sirf sabse liquid F&O stocks (kam slippage). Pipeline me `exits.sl_atr` aur timeframe badal ke yahi test kiya ja sakta hai.
Jo bhi future me pass ho, pehle paper trading.
