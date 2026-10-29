# Why not a trade every day?

Window 2024-11-13 -> 2026-10-26 (481 trading days), fixed Rs 50,000 per trade, after charges

## 1) FINAL version: where each day stops

| stage                                                            |   days |   % of days |
|:-----------------------------------------------------------------|-------:|------------:|
| 1. Monday (skipped)                                              |     98 |        20.4 |
| 2. Nifty mixed at 10:15 (above close but below open, or reverse) |    113 |        23.5 |
| 3. Today's Nifty direction is against the 20-day trend           |    133 |        27.7 |
| 4. No sector stronger than Nifty                                 |      4 |         0.8 |
| 5. No non-F&O stock in those sectors passes move/volume/VWAP     |     52 |        10.8 |
| 6. Stocks found, but no pullback entry by 13:30                  |     11 |         2.3 |
| 7. TRADE                                                         |     70 |        14.6 |

## 2) Loosening rules step by step (each row keeps the changes above it, except row 7)

| version                                                       |   days_with_trade_% |   trades |   trades_per_month |     net |   avg_per_trade |   win_% |   max_dd |   charges | months_profit   |   year1 |   year2 |
|:--------------------------------------------------------------|--------------------:|---------:|-------------------:|--------:|----------------:|--------:|---------:|----------:|:----------------|--------:|--------:|
| 1. FINAL                                                      |                  15 |      112 |                4.9 |   11398 |             102 |    46.4 |    -3113 |      7262 | 16/23           |    4268 |    7130 |
| 2. + trade Monday too                                         |                  19 |      144 |                6.3 |    5660 |              39 |    45.8 |    -5311 |      9337 | 14/24           |    -683 |    6343 |
| 3. + direction from 20-day trend only (ignore Nifty at 10:15) |                  38 |      305 |               13.3 |   -4889 |             -16 |    43.3 |   -10806 |     19772 | 9/24            |   -4507 |    -382 |
| 4. + top 3 sectors (was 2)                                    |                  49 |      437 |               19.1 |  -28739 |             -66 |    42.3 |   -31537 |     28330 | 8/24            |  -13387 |  -15352 |
| 5. + stock move 0.5-4%, volume 1.2x                           |                  69 |      755 |               33   |  -98032 |            -130 |    38.9 |  -100404 |     48928 | 5/24            |  -48108 |  -49924 |
| 6. + enter at 10:15 (no pullback wait)                        |                  73 |      927 |               40.5 | -108125 |            -117 |    40.7 |  -110833 |     60076 | 6/24            |  -60923 |  -47202 |
| 7. Every day: only Nifty-direction + top stock, enter 10:15   |                  72 |     1717 |               75   | -163604 |             -95 |    43.2 |  -184654 |    111251 | 6/24            |  -22984 | -140620 |