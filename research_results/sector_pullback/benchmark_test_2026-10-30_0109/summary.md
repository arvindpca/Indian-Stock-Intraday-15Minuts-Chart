# Candidates vs intraday benchmarks

Window 2024-11-13 -> 2026-10-26 (481 days ~ 2.0 years), Rs 100,000 per trade, 289 non-F&O, skip Monday
Benchmarks: avg/trade >= 0.2% ok, >= 0.4% good | PF >= 1.3 ok, >= 1.7 good | profit months >= 60% | yearly return >= 12% ok, >= 25% good | max DD <= 20% of capital

## Rs 20/order

| version                                         |   trades/month |   avg/trade % |   PF |   win % |   profit months % |   capital needed |   max DD % of capital |   yearly return % |   Y1 Rs |   Y2 Rs |   total Rs |
|:------------------------------------------------|---------------:|--------------:|-----:|--------:|------------------:|-----------------:|----------------------:|------------------:|--------:|--------:|-----------:|
| A. FINAL                                        |            4.7 |          0.28 | 1.8  |    48.1 |                70 |           400000 |                  -1.5 |               3.8 |   13696 |   16227 |      29923 |
| B. FINAL, max 2 trades/day                      |            4   |          0.33 | 1.97 |    48.4 |                65 |           200000 |                  -2.2 |               7.6 |   13303 |   16675 |      29978 |
| E. FINAL + NiftyEMA + POC                       |            3.3 |          0.38 | 2.35 |    53.3 |                77 |           400000 |                  -1.2 |               3.6 |   17329 |   11299 |      28628 |
| E2. FINAL + NiftyEMA + POC, max 2/day           |            3   |          0.47 | 2.83 |    55.9 |                77 |           200000 |                  -1.7 |               8.1 |   16823 |   15029 |      31852 |
| F2. FINAL + NiftyEMA + POC + ATR>=3%, max 2/day |            1.9 |          0.63 | 3.39 |    54.5 |                75 |           200000 |                  -1.3 |               7   |   14844 |   12822 |      27666 |
| G2. 5-min + NiftyEMA + POC, max 2/day           |            7.7 |          0.21 | 1.61 |    50   |                71 |           200000 |                  -4.4 |               9.3 |   22688 |   13929 |      36617 |

## Zero brokerage

| version                                         |   trades/month |   avg/trade % |   PF |   win % |   profit months % |   capital needed |   max DD % of capital |   yearly return % |   Y1 Rs |   Y2 Rs |   total Rs |
|:------------------------------------------------|---------------:|--------------:|-----:|--------:|------------------:|-----------------:|----------------------:|------------------:|--------:|--------:|-----------:|
| A. FINAL                                        |            4.7 |          0.32 | 2.01 |    49.1 |                74 |           400000 |                  -1.4 |               4.5 |   16434 |   18586 |      35020 |
| B. FINAL, max 2 trades/day                      |            4   |          0.38 | 2.2  |    49.5 |                74 |           200000 |                  -2.1 |               8.7 |   15663 |   18611 |      34274 |
| E. FINAL + NiftyEMA + POC                       |            3.3 |          0.43 | 2.64 |    56   |                82 |           400000 |                  -1.1 |               4.1 |   19123 |   13045 |      32168 |
| E2. FINAL + NiftyEMA + POC, max 2/day           |            3   |          0.52 | 3.18 |    58.8 |                82 |           200000 |                  -1.7 |               8.9 |   18522 |   16539 |      35061 |
| F2. FINAL + NiftyEMA + POC + ATR>=3%, max 2/day |            1.9 |          0.68 | 3.8  |    54.5 |                75 |           200000 |                  -1.2 |               7.6 |   15930 |   13813 |      29743 |
| G2. 5-min + NiftyEMA + POC, max 2/day           |            7.7 |          0.26 | 1.8  |    52.8 |                71 |           200000 |                  -4.1 |              11.4 |   27030 |   17894 |      44924 |
