# ============================================================
# MAIN
# ============================================================
def main():
    print()
    print("=" * 78)
    print(" INDIAN NSE - TRADINGVIEW V1 SMC MIRROR")
    print(" Angel One SmartAPI | NSE F&O Stocks")
    print(" Liquidity -> Sweep -> Displacement -> MSS -> FVG/OB")
    print(" -> Retest -> Confirmation -> Volume -> ENTRY")
    print(" TIME RESTRICTION: DISABLED (ALL DAY)")
    print("=" * 78)

    print(
        "Current:",
        datetime.now(IST).strftime(
            "%Y-%m-%d %H:%M:%S IST"
        ),
    )
    print(f"Refresh: {REFRESH_MINUTES} minutes")
    print(f"Volume filter: {USE_VOLUME_FILTER}")
    print(f"HTF filter: {USE_HTF_FILTER}")
    print("=" * 78)

    client = get_client()

    instruments = load_instruments()
    stocks = get_nse_fno_stocks(instruments)

    if not stocks:
        raise RuntimeError(
            "No NSE F&O stocks found in Angel One instrument master."
        )

    print(f"NSE F&O stocks found: {len(stocks)}")

    if MAX_STOCKS > 0:
        print(f"MAX_STOCKS: {MAX_STOCKS}")

    scan_number = 0

    while True:
        scan_number += 1

        print()
        print("=" * 78)
        print(f" NSE F&O GLOBAL SCAN #{scan_number}")
        print(
            datetime.now(IST).strftime(
                "%Y-%m-%d %H:%M:%S IST"
            )
        )
        print("=" * 78)

        signal_count = 0

        for n, stock in enumerate(stocks, 1):
            try:
                print(
                    f"[{n}/{len(stocks)}] "
                    f"Scanning {stock['symbol']} ..."
                )

                result = scan_stock(client, stock)
                print_result(result)

                if result.get("signal") in ("LONG", "SHORT"):
                    signal_count += 1
                    key = signal_key(result)

                    if key not in SENT_SIGNALS:
                        message = build_telegram_message(result)

                        if send_telegram_message(message):
                            SENT_SIGNALS.add(key)
                    else:
                        print(
                            "Telegram: DUPLICATE SIGNAL SKIPPED"
                        )

                # Stay below aggressive API request rates.
                time.sleep(API_DELAY_SECONDS)

            except Exception as e:
                print()
                print("-" * 78)
                print(f"{stock['symbol']} ERROR: {e}")
                print("-" * 78)

                # Give API a little breathing room after errors.
                time.sleep(1.0)

        print()
        print("=" * 78)
        print("NSE F&O SCAN COMPLETE")
        print(f"Stocks scanned : {len(stocks)}")
        print(f"Entry signals  : {signal_count}")
        print(
            f"Next refresh in {REFRESH_MINUTES} minutes..."
        )
        print("=" * 78)

        try:
            time.sleep(REFRESH_MINUTES * 60)
        except KeyboardInterrupt:
            print("\nScanner stopped by user.")
            break


if __name__ == "__main__":
    main()
