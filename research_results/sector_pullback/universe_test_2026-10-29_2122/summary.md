# Sector Pullback - final settings on 3 universes

IS 2023-10-12 -> 2025-10-17 (Nifty +30%) | OOS 2025-10-20 -> 2026-10-26 (Nifty -12%)
Rs 1,000 risk sizing (to pullback stop), max Rs 2 L per trade, after charges.

| universe                               | emergency_SL   |   stocks |   IS_per_month |   IS_net |   IS_pf |   OOS_per_month |   OOS_net |   OOS_pf |   OOS_dd |
|:---------------------------------------|:---------------|---------:|---------------:|---------:|--------:|----------------:|----------:|---------:|---------:|
| 182 list (fixed, non-F&O)              | none           |      182 |            2.4 |    10802 |    1.26 |             5.4 |     31007 |     1.69 |    -8175 |
| 182 list (fixed, non-F&O)              | 3%             |      182 |            2.4 |    11132 |    1.27 |             5.4 |     11648 |     1.2  |    -9732 |
| 182 list (fixed, non-F&O)              | 2%             |      182 |            2.4 |    -3351 |    0.93 |             5.4 |     13138 |     1.24 |    -9487 |
| All non-F&O in Nifty 500 (289)         | none           |      283 |            3.1 |    14137 |    1.3  |             6   |     25545 |     1.49 |    -9081 |
| All non-F&O in Nifty 500 (289)         | 3%             |      283 |            3.1 |    14467 |    1.31 |             6   |      6186 |     1.1  |   -10637 |
| All non-F&O in Nifty 500 (289)         | 2%             |      283 |            3.1 |      -16 |    1    |             6   |      7676 |     1.12 |   -10722 |
| All Nifty 500 (F&O stocks in cash too) | none           |      495 |            7.4 |    -7787 |    0.94 |            17.5 |     -5596 |     0.97 |   -33967 |
| All Nifty 500 (F&O stocks in cash too) | 3%             |      495 |            7.4 |    -4110 |    0.97 |            17.5 |    -24605 |     0.86 |   -38295 |
| All Nifty 500 (F&O stocks in cash too) | 2%             |      495 |            7.4 |   -16911 |    0.87 |            17.5 |    -22287 |     0.87 |   -35976 |

## Long / short, worst trade

| universe                               | emergency_SL   |   IS_L |   IS_S |   OOS_L |   OOS_S |   IS_worst |   OOS_worst |   IS_win |   OOS_win |
|:---------------------------------------|:---------------|-------:|-------:|--------:|--------:|-----------:|------------:|---------:|----------:|
| 182 list (fixed, non-F&O)              | none           |  18141 |  -7339 |   17338 |   13669 |      -4945 |       -3865 |     50   |      48.4 |
| 182 list (fixed, non-F&O)              | 3%             |  18141 |  -7009 |    3438 |    8211 |      -5080 |       -6172 |     50   |      45.3 |
| 182 list (fixed, non-F&O)              | 2%             |   2860 |  -6210 |    6240 |    6898 |      -4179 |       -4178 |     48.3 |      45.3 |
| All non-F&O in Nifty 500 (289)         | none           |  21801 |  -7664 |   12722 |   12823 |      -4945 |       -3865 |     50.7 |      45.1 |
| All non-F&O in Nifty 500 (289)         | 3%             |  21801 |  -7334 |   -1178 |    7365 |      -5080 |       -6172 |     50.7 |      42.3 |
| All non-F&O in Nifty 500 (289)         | 2%             |   6520 |  -6536 |    1624 |    6052 |      -4179 |       -4178 |     49.3 |      42.3 |
| All Nifty 500 (F&O stocks in cash too) | none           |  -4515 |  -3273 |  -20541 |   14945 |      -6199 |       -6471 |     46.6 |      45.7 |
| All Nifty 500 (F&O stocks in cash too) | 3%             |  -1168 |  -2942 |  -34442 |    9836 |      -5080 |       -6172 |     46.6 |      44.7 |
| All Nifty 500 (F&O stocks in cash too) | 2%             | -14432 |  -2479 |  -30355 |    8068 |      -4179 |       -4178 |     46   |      44.7 |

## Share of months in profit

|                                                 |   months_in_profit |
|:------------------------------------------------|-------------------:|
| ('182 list (fixed, non-F&O)', 0.0)              |               0.62 |
| ('182 list (fixed, non-F&O)', 2.0)              |               0.58 |
| ('182 list (fixed, non-F&O)', 3.0)              |               0.62 |
| ('All Nifty 500 (F&O stocks in cash too)', 0.0) |               0.46 |
| ('All Nifty 500 (F&O stocks in cash too)', 2.0) |               0.42 |
| ('All Nifty 500 (F&O stocks in cash too)', 3.0) |               0.46 |
| ('All non-F&O in Nifty 500 (289)', 0.0)         |               0.5  |
| ('All non-F&O in Nifty 500 (289)', 2.0)         |               0.42 |
| ('All non-F&O in Nifty 500 (289)', 3.0)         |               0.5  |