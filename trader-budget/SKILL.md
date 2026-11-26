---
name: trader-budget
description: Size an unleveraged NSE cash-equity paper position in whole shares using INR capital, stop-loss budget and estimated Indian transaction costs.
---

# Trader Budget

Use the user's paper account configuration and existing ledger. Keep `examples/config.json` and a separate ledger for the offline walkthrough. The ledger records its initial capital on first use. [Configuration and fee model](../docs/data-and-workflow.md)

```sh
python3 -m india_trader --config "$CONFIG" paper-status --db "$PAPER_DB" --asof "$ASOF"
python3 -m india_trader --config "$CONFIG" ticket "$SYMBOL" --setup "$SETUP" \
  --target-r "$TARGET_R" --bars "$BARS" --benchmark "$BENCHMARK" \
  --instruments "$INSTRUMENTS" --db "$PAPER_DB" --asof "$ASOF"
```

Run from the checkout. Give `ticket` the same signal inputs and target multiple as `trader-exit`. Supply an offset-aware cutoff, completed candles and documented same-day instrument evidence.

When the strategy allows an unbenchmarked signal and the entry workflow used none, omit `--benchmark`. Repeat `--bars` when benchmark and equity history are in separate files.

The sizing calculation uses the maximum modeled entry, the slipped stop fill, and one round trip of estimated fees. Available cash after reserves and the per-position cash cap limit full notional. Per-trade risk and remaining day loss capacity limit loss at the modeled stop. The estimate covers brokerage, exchange charges, SEBI turnover fees, GST, sell-side STT and buy-side stamp duty. Price slippage is modeled separately. Check the dated assumptions when using a different schedule. [Indian cost sources](../docs/india-market.md)

Show share count, expected and maximum entry, stop and modeled stop fill, target, reserved cash, planned INR loss and gain, estimated charges, net reward/risk, expiry and digest. If `ticket` blocks, report its reason and relevant configured limit. Retain the user's limits for any new calculation.

Pass the same ticket and ledger to `trader-risk` for review or `trader-broker` for exact-digest paper confirmation.
