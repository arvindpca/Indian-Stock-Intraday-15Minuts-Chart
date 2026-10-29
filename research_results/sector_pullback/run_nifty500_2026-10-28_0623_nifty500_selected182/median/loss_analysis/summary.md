# Loss analysis

Trades analysed: 146 (source: research_results/sector_pullback/run_nifty500_2026-10-28_0623_nifty500_selected182/median/trades_best.csv)

## IS

- Trades 66, net Rs -5,812
- SL trades 39: never moved +0.5R in our favour = 59%, never moved +1R = 79%
- SL trades where price LATER reached our target the same day (stop hunted): 21% (8)
- Hold till 15:10 with no SL/target: avg +0.63 R per trade, positive 58%
- Median MAE (worst move against, whole day): 1.70 R

What-if: same entries, fixed SL %, target 2x SL, after charges (Rs):

|          |   SL 0.5% |   SL 0.75% |   SL 1.0% |   SL 1.5% |
|:---------|----------:|-----------:|----------:|----------:|
| net      | -2391     |    14815   |  7024     |   -745    |
| win_rate |     0.379 |        0.5 |     0.515 |      0.53 |

MFE of SL trades (how far they went our way first):

| MFE_R        |   trades |
|:-------------|---------:|
| (-1.0, 0.25] |       16 |
| (0.25, 0.5]  |        7 |
| (0.5, 1.0]   |        8 |
| (1.0, 1.5]   |        5 |
| (1.5, 99.0]  |        3 |

By side:

| side   |       net |   eod_R |
|:-------|----------:|--------:|
| LONG   | -11031.8  |    0.59 |
| SHORT  |   5219.49 |    0.7  |

## OOS

- Trades 80, net Rs -7,355
- SL trades 45: never moved +0.5R in our favour = 62%, never moved +1R = 84%
- SL trades where price LATER reached our target the same day (stop hunted): 16% (7)
- Hold till 15:10 with no SL/target: avg +0.36 R per trade, positive 52%
- Median MAE (worst move against, whole day): 1.44 R

What-if: same entries, fixed SL %, target 2x SL, after charges (Rs):

|          |   SL 0.5% |   SL 0.75% |   SL 1.0% |   SL 1.5% |
|:---------|----------:|-----------:|----------:|----------:|
| net      | -4376     |    468     | -2497     | -3154     |
| win_rate |     0.362 |      0.412 |     0.412 |     0.438 |

MFE of SL trades (how far they went our way first):

| MFE_R        |   trades |
|:-------------|---------:|
| (-1.0, 0.25] |       14 |
| (0.25, 0.5]  |       14 |
| (0.5, 1.0]   |       10 |
| (1.0, 1.5]   |        5 |
| (1.5, 99.0]  |        2 |

By side:

| side   |       net |   eod_R |
|:-------|----------:|--------:|
| LONG   | -14679.8  |    0.28 |
| SHORT  |   7324.92 |    0.5  |
