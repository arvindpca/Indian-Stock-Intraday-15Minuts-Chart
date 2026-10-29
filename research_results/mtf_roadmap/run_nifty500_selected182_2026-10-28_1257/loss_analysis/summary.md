# Loss analysis

Trades analysed: 1303 (source: research_results/mtf_roadmap/run_nifty500_selected182_2026-10-28_1257/trades_best.csv)

## IS

- Trades 859, net Rs -67,276
- SL trades 178: never moved +0.5R in our favour = 92%, never moved +1R = 99%
- SL trades where price LATER reached our target the same day (stop hunted): 2% (3)
- Hold till 15:10 with no SL/target: avg -0.00 R per trade, positive 50%
- Median MAE (worst move against, whole day): 0.52 R

What-if: same entries, fixed SL %, target 2x SL, after charges (Rs):

|          |    SL 0.5% |   SL 0.75% |    SL 1.0% |    SL 1.5% |
|:---------|-----------:|-----------:|-----------:|-----------:|
| net      | -68331     | -39501     | -49570     | -57179     |
| win_rate |      0.371 |      0.414 |      0.433 |      0.447 |

MFE of SL trades (how far they went our way first):

| MFE_R        |   trades |
|:-------------|---------:|
| (-1.0, 0.25] |      122 |
| (0.25, 0.5]  |       41 |
| (0.5, 1.0]   |       14 |
| (1.0, 1.5]   |        1 |

By side:

| side   |      net |   eod_R |
|:-------|---------:|--------:|
| LONG   | -33343.9 |    0.06 |
| SHORT  | -33932.4 |   -0.05 |

## OOS

- Trades 444, net Rs -28,677
- SL trades 82: never moved +0.5R in our favour = 78%, never moved +1R = 99%
- SL trades where price LATER reached our target the same day (stop hunted): 0% (0)
- Hold till 15:10 with no SL/target: avg +0.01 R per trade, positive 53%
- Median MAE (worst move against, whole day): 0.48 R

What-if: same entries, fixed SL %, target 2x SL, after charges (Rs):

|          |   SL 0.5% |   SL 0.75% |   SL 1.0% |    SL 1.5% |
|:---------|----------:|-----------:|----------:|-----------:|
| net      | -8077     |    6513    | -9554     | -11184     |
| win_rate |     0.401 |       0.45 |     0.453 |      0.473 |

MFE of SL trades (how far they went our way first):

| MFE_R        |   trades |
|:-------------|---------:|
| (-1.0, 0.25] |       47 |
| (0.25, 0.5]  |       17 |
| (0.5, 1.0]   |       17 |
| (1.5, 99.0]  |        1 |

By side:

| side   |      net |   eod_R |
|:-------|---------:|--------:|
| LONG   | -14404.5 |    0.04 |
| SHORT  | -14272.9 |   -0.02 |
