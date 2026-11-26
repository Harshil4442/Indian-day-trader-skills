---
name: trader-exit
description: Plan the structural stop, target and IST flatten deadline for a fresh NSE cash-equity intraday signal in India Day Trader.
---

# Trader Exit

Run from the India Day Trader checkout. Start with the same five-minute candles, benchmark, cutoff and strategy config that produced the entry report. [Candle and session contract](../docs/data-and-workflow.md)

```sh
python3 -m india_trader --config "$CONFIG" plan "$SYMBOL" --setup "$SETUP" \
  --bars "$BARS" --benchmark "$BENCHMARK" --asof "$ASOF" --target-r "$TARGET_R"
```

Pass additional `--bars` files for history or the benchmark. Use an offset-aware cutoff and an optional `--calendar` before the command when the packaged calendar lacks the date. Keep the aligned benchmark if the signal used one.

- For `orb`, the structural stop is the midpoint of the opening range.
- For `vwap-reclaim`, it is the lower low of the trigger candle and the previous candle.
- For `trend-pullback`, it is the lowest low of the two pullback candles.

Show the signal time in IST, close used as the reference entry, structural stop, price risk per share, target and gross R multiple. The nominal target is entry plus price risk times `--target-r`. The CLI defaults to 2R. Show the configured flat deadline (15:05 IST by default) and use the paper ticket's `flatten_at` for the effective deadline on a shorter modeled day.

A `plan` is a research result. The paper ticket applies tick rounding, entry tolerance, estimated costs and slippage. An exit can fail or lose more than the modeled stop loss in a gap, halt or locked band. If the CLI reports no fresh confirmation, return the reason from `entry` and retain the setup parameters. [Exit and fill assumptions](../docs/data-and-workflow.md)

Hand the same symbol, setup, target multiple and cutoff to `trader-timemachine` for replay and to `trader-budget` for INR sizing.
