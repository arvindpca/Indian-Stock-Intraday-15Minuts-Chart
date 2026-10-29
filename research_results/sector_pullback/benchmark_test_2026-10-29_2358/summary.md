# Candidates vs intraday benchmarks

Window 2024-11-13 -> 2026-10-26 (481 days ~ 2.0 years), Rs 100,000 per trade, 289 non-F&O, skip Monday
Benchmarks: avg/trade >= 0.2% ok, >= 0.4% good | PF >= 1.3 ok, >= 1.7 good | profit months >= 60% | yearly return >= 12% ok, >= 25% good | max DD <= 20% of capital

## Rs 20/order

| version                                         |   trades/month |   avg/trade % |   PF |   win % |   profit months % |   capital needed |   max DD % of capital |   yearly return % |   Y1 Rs |   Y2 Rs |   total Rs |
|:------------------------------------------------|---------------:|--------------:|-----:|--------:|------------------:|-----------------:|----------------------:|------------------:|--------:|--------:|-----------:|
| A. FINAL                                        |            4.9 |          0.25 | 1.71 |    48.2 |                70 |           500000 |                  -1.2 |               2.9 |   11455 |   16720 |      28175 |
| B. FINAL, max 2 trades/day                      |            4.1 |          0.29 | 1.83 |    47.9 |                65 |           200000 |                  -2.2 |               7   |   10367 |   17170 |      27537 |
| E. FINAL + NiftyEMA + POC                       |            3.4 |          0.35 | 2.19 |    53.2 |                73 |           400000 |                  -1.2 |               3.5 |   15539 |   11721 |      27260 |
| E2. FINAL + NiftyEMA + POC, max 2/day           |            3   |          0.47 | 2.85 |    56.5 |                77 |           200000 |                  -1.7 |               8.2 |   16823 |   15452 |      32275 |
| F2. FINAL + NiftyEMA + POC + ATR>=3%, max 2/day |            2   |          0.62 | 3.42 |    55.6 |                75 |           200000 |                  -1.3 |               7.2 |   14844 |   13245 |      28089 |
| G2. 5-min + NiftyEMA + POC, max 2/day           |            7.9 |          0.16 | 1.44 |    48.6 |                71 |           200000 |                  -4.8 |               7.4 |   18653 |   10402 |      29055 |

## Zero brokerage

| version                                         |   trades/month |   avg/trade % |   PF |   win % |   profit months % |   capital needed |   max DD % of capital |   yearly return % |   Y1 Rs |   Y2 Rs |   total Rs |
|:------------------------------------------------|---------------:|--------------:|-----:|--------:|------------------:|-----------------:|----------------------:|------------------:|--------:|--------:|-----------:|
| A. FINAL                                        |            4.9 |          0.3  | 1.91 |    49.1 |                74 |           500000 |                  -1.1 |               3.4 |   14287 |   19175 |      33462 |
| B. FINAL, max 2 trades/day                      |            4.1 |          0.34 | 2.04 |    48.9 |                74 |           200000 |                  -2.1 |               8.1 |   12774 |   19200 |      31974 |
| E. FINAL + NiftyEMA + POC                       |            3.4 |          0.4  | 2.45 |    55.8 |                77 |           400000 |                  -1.1 |               3.9 |   17380 |   13515 |      30895 |
| E2. FINAL + NiftyEMA + POC, max 2/day           |            3   |          0.51 | 3.21 |    59.4 |                82 |           200000 |                  -1.7 |               9   |   18522 |   17009 |      35531 |
| F2. FINAL + NiftyEMA + POC + ATR>=3%, max 2/day |            2   |          0.67 | 3.84 |    55.6 |                75 |           200000 |                  -1.2 |               7.7 |   15930 |   14283 |      30213 |
| G2. 5-min + NiftyEMA + POC, max 2/day           |            7.9 |          0.21 | 1.6  |    51.4 |                71 |           200000 |                  -4.5 |               9.6 |   23090 |   14508 |      37598 |
