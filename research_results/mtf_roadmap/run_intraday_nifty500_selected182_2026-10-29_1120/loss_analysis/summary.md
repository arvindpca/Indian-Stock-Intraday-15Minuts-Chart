# Loss analysis

Trades analysed: 1308 (source: research_results/mtf_roadmap/run_intraday_nifty500_selected182_2026-10-29_1120/trades_best.csv)

## IS

- Trades 865, net Rs -99,246
- SL trades 537: never moved +0.5R in our favour = 59%, never moved +1R = 79%
- SL trades where price LATER reached our target the same day (stop hunted): 16% (86)
- Hold till 15:10 with no SL/target: avg -0.05 R per trade, positive 48%
- Median MAE (worst move against, whole day): 1.67 R

What-if: same entries, fixed SL %, target 2x SL, after charges (Rs):

|          |    SL 0.5% |   SL 0.75% |    SL 1.0% |    SL 1.5% |
|:---------|-----------:|-----------:|-----------:|-----------:|
| net      | -53151     |   -40192   | -50665     | -83035     |
| win_rate |      0.366 |        0.4 |      0.418 |      0.425 |

MFE of SL trades (how far they went our way first):

| MFE_R        |   trades |
|:-------------|---------:|
| (-1.0, 0.25] |      226 |
| (0.25, 0.5]  |       94 |
| (0.5, 1.0]   |      104 |
| (1.0, 1.5]   |       58 |
| (1.5, 99.0]  |       55 |

By side:

| side   |      net |   eod_R |
|:-------|---------:|--------:|
| LONG   | -83496.2 |    0.06 |
| SHORT  | -15749.3 |   -0.18 |

## OOS

- Trades 443, net Rs -107,138
- SL trades 303: never moved +0.5R in our favour = 57%, never moved +1R = 78%
- SL trades where price LATER reached our target the same day (stop hunted): 17% (53)
- Hold till 15:10 with no SL/target: avg -0.12 R per trade, positive 46%
- Median MAE (worst move against, whole day): 2.01 R

What-if: same entries, fixed SL %, target 2x SL, after charges (Rs):

|          |    SL 0.5% |   SL 0.75% |    SL 1.0% |    SL 1.5% |
|:---------|-----------:|-----------:|-----------:|-----------:|
| net      | -91474     | -58596     | -75279     | -54003     |
| win_rate |      0.312 |      0.352 |      0.361 |      0.404 |

MFE of SL trades (how far they went our way first):

| MFE_R        |   trades |
|:-------------|---------:|
| (-1.0, 0.25] |      111 |
| (0.25, 0.5]  |       61 |
| (0.5, 1.0]   |       64 |
| (1.0, 1.5]   |       27 |
| (1.5, 99.0]  |       40 |

By side:

| side   |      net |   eod_R |
|:-------|---------:|--------:|
| LONG   | -57163.7 |   -0.12 |
| SHORT  | -49974   |   -0.13 |
