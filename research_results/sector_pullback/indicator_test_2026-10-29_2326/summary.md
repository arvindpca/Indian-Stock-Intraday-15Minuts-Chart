# Indicator / S-R / EMA / volume / POC / Nifty filters

Window 2024-11-13 -> 2026-10-26, Rs 50,000 per trade, Rs 20/order. Y1 = first year, Y2 = second.
A filter keeps only trades where the condition was TRUE at entry. 'both' = better avg per trade in both years AND net profit in both years.

## Pool FINAL (112 trades)

| filter                                                 |   trades |   Y1_net |   Y2_net |   Y1_avg |   Y2_avg |   kept_% | both   |
|:-------------------------------------------------------|---------:|---------:|---------:|---------:|---------:|---------:|:-------|
| (no filter)                                            |      112 |     4268 |     7130 |       71 |      137 |      100 |        |
| EMA: stock above 5-min EMA50                           |      107 |     3867 |     6904 |       69 |      135 |       96 |        |
| EMA: stock daily trend (prev close vs 20-day EMA)      |       68 |     4431 |     4838 |      117 |      161 |       61 | YES    |
| EMA: stock daily trend (prev close vs 50-day EMA)      |       52 |     5121 |     5240 |      205 |      194 |       46 | YES    |
| S/R: already broke prev-day high (long) / low (short)  |       85 |      839 |     4397 |       19 |      107 |       76 |        |
| S/R: >=1% room to prev-day high/low, or already broken |      103 |     3765 |     5468 |       70 |      112 |       92 |        |
| S/R: broke opening-range (30 min) high/low             |       58 |    -1520 |     3944 |      -58 |      123 |       52 |        |
| S/R: within 0.5% of day high (long) / low (short)      |       27 |     1739 |     4615 |      145 |      308 |       24 | YES    |
| VOL: signal candle volume >= 1.5x last 20 candles      |       13 |     -654 |     1131 |     -131 |      141 |       12 |        |
| VOL: signal candle volume >= 1.0x last 20 candles      |       20 |    -1397 |      703 |     -140 |       70 |       18 |        |
| POC: price above prev-day POC (long) / below (short)   |      108 |     4541 |     7375 |       78 |      148 |       96 | YES    |
| POC: price above today's POC (long) / below (short)    |       75 |     4726 |     4575 |      121 |      127 |       67 |        |
| RSI: 5-min RSI 55-70 (long) / 30-45 (short)            |       92 |     1595 |     4623 |       32 |      110 |       82 |        |
| RSI: 5-min RSI not extreme (<75 long, >25 short)       |      111 |     4268 |     7944 |       71 |      156 |       99 |        |
| VWAP: entry within 1% of VWAP                          |       88 |     2391 |     1496 |       48 |       39 |       79 |        |
| NIFTY: 5-min close on our side of its EMA20            |       74 |     5370 |     7258 |      138 |      207 |       66 | YES    |
| NIFTY: moved >= 0.3% our way at entry                  |       59 |     3040 |     3387 |       95 |      125 |       53 |        |
| NIFTY: prev close on our side of 50-day EMA            |       97 |     4068 |     6494 |       77 |      148 |       87 | YES    |

### FINAL: pairs of filters that passed alone

| filter A                                             | filter B                                             |   trades |   Y1_net |   Y2_net |
|:-----------------------------------------------------|:-----------------------------------------------------|---------:|---------:|---------:|
| POC: price above prev-day POC (long) / below (short) | NIFTY: prev close on our side of 50-day EMA          |       93 |     4340 |     6739 |
| POC: price above prev-day POC (long) / below (short) | NIFTY: 5-min close on our side of its EMA20          |       72 |     5557 |     7081 |
| EMA: stock daily trend (prev close vs 20-day EMA)    | POC: price above prev-day POC (long) / below (short) |       67 |     4431 |     5260 |
| NIFTY: 5-min close on our side of its EMA20          | NIFTY: prev close on our side of 50-day EMA          |       64 |     4882 |     7220 |
| EMA: stock daily trend (prev close vs 20-day EMA)    | NIFTY: prev close on our side of 50-day EMA          |       61 |     4936 |     5231 |
| EMA: stock daily trend (prev close vs 50-day EMA)    | POC: price above prev-day POC (long) / below (short) |       52 |     5121 |     5240 |
| EMA: stock daily trend (prev close vs 20-day EMA)    | EMA: stock daily trend (prev close vs 50-day EMA)    |       50 |     5121 |     4922 |
| EMA: stock daily trend (prev close vs 50-day EMA)    | NIFTY: prev close on our side of 50-day EMA          |       47 |     5230 |     5083 |
| EMA: stock daily trend (prev close vs 20-day EMA)    | NIFTY: 5-min close on our side of its EMA20          |       42 |     3748 |     4857 |
| EMA: stock daily trend (prev close vs 50-day EMA)    | NIFTY: 5-min close on our side of its EMA20          |       32 |     2675 |     4844 |
| S/R: within 0.5% of day high (long) / low (short)    | NIFTY: prev close on our side of 50-day EMA          |       26 |     1739 |     4389 |
| S/R: within 0.5% of day high (long) / low (short)    | POC: price above prev-day POC (long) / below (short) |       25 |     1926 |     5037 |
| S/R: within 0.5% of day high (long) / low (short)    | NIFTY: 5-min close on our side of its EMA20          |       20 |     1888 |     3929 |
| EMA: stock daily trend (prev close vs 20-day EMA)    | S/R: within 0.5% of day high (long) / low (short)    |       16 |     2041 |     3549 |
| EMA: stock daily trend (prev close vs 50-day EMA)    | S/R: within 0.5% of day high (long) / low (short)    |       10 |     2789 |     4253 |

## Pool SCAN5 (373 trades)

| filter                                                 |   trades |   Y1_net |   Y2_net |   Y1_avg |   Y2_avg |   kept_% | both   |
|:-------------------------------------------------------|---------:|---------:|---------:|---------:|---------:|---------:|:-------|
| (no filter)                                            |      373 |    -1111 |     2560 |       -6 |       14 |      100 |        |
| EMA: stock above 5-min EMA50                           |      368 |    -1512 |     2334 |       -8 |       13 |       99 |        |
| EMA: stock daily trend (prev close vs 20-day EMA)      |      228 |    -1462 |     2871 |      -13 |       25 |       61 |        |
| EMA: stock daily trend (prev close vs 50-day EMA)      |      193 |     1667 |     3516 |       20 |       33 |       52 | YES    |
| S/R: already broke prev-day high (long) / low (short)  |      263 |    -2911 |     1670 |      -22 |       13 |       71 |        |
| S/R: >=1% room to prev-day high/low, or already broken |      329 |    -2072 |     4433 |      -12 |       27 |       88 |        |
| S/R: broke opening-range (30 min) high/low             |      224 |    -7171 |    -1536 |      -65 |      -14 |       60 |        |
| S/R: within 0.5% of day high (long) / low (short)      |       92 |      638 |     1724 |       15 |       34 |       25 | YES    |
| VOL: signal candle volume >= 1.5x last 20 candles      |       50 |     -956 |    -4595 |      -46 |     -158 |       13 |        |
| VOL: signal candle volume >= 1.0x last 20 candles      |       78 |    -4442 |     -861 |     -120 |      -21 |       21 |        |
| POC: price above prev-day POC (long) / below (short)   |      356 |     -548 |     2591 |       -3 |       16 |       95 |        |
| POC: price above today's POC (long) / below (short)    |      259 |      682 |     3755 |        5 |       29 |       69 | YES    |
| RSI: 5-min RSI 55-70 (long) / 30-45 (short)            |      300 |    -2482 |      737 |      -16 |        5 |       80 |        |
| RSI: 5-min RSI not extreme (<75 long, >25 short)       |      371 |    -1111 |     3559 |       -6 |       20 |       99 |        |
| VWAP: entry within 1% of VWAP                          |      302 |    -1677 |     -124 |      -10 |       -1 |       81 |        |
| NIFTY: 5-min close on our side of its EMA20            |      240 |     6053 |     3354 |       48 |       29 |       64 | YES    |
| NIFTY: moved >= 0.3% our way at entry                  |      231 |     4979 |    -3715 |       42 |      -33 |       62 |        |
| NIFTY: prev close on our side of 50-day EMA            |      316 |     -443 |      187 |       -3 |        1 |       85 |        |

### SCAN5: pairs of filters that passed alone

| filter A                                            | filter B                                            |   trades |   Y1_net |   Y2_net |
|:----------------------------------------------------|:----------------------------------------------------|---------:|---------:|---------:|
| POC: price above today's POC (long) / below (short) | NIFTY: 5-min close on our side of its EMA20         |      175 |     7570 |     6807 |
| EMA: stock daily trend (prev close vs 50-day EMA)   | POC: price above today's POC (long) / below (short) |      123 |      834 |     3408 |
| EMA: stock daily trend (prev close vs 50-day EMA)   | NIFTY: 5-min close on our side of its EMA20         |      120 |      151 |     2173 |
| S/R: within 0.5% of day high (long) / low (short)   | POC: price above today's POC (long) / below (short) |       81 |     -169 |     1053 |
| S/R: within 0.5% of day high (long) / low (short)   | NIFTY: 5-min close on our side of its EMA20         |       60 |     2370 |     4220 |
| EMA: stock daily trend (prev close vs 50-day EMA)   | S/R: within 0.5% of day high (long) / low (short)   |       50 |     4616 |     4188 |
