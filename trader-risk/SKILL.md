---
name: trader-risk
description: Check an NSE cash-equity paper ticket against current exchange eligibility, tick and price bands, IST deadlines and local account risk limits.
---

# Trader Risk

Run from the checkout with the user's risk config, current regular-session candles, the matching benchmark where used, and the same local ledger as earlier paper actions. Use the actual current cutoff for current trading research and an explicit offset-aware cutoff for historical scenarios. [Input formats](../docs/data-and-workflow.md)

```sh
python3 -m india_trader calendar --asof "$ASOF"
python3 -m india_trader --config "$CONFIG" ticket "$SYMBOL" --setup "$SETUP" \
  --target-r "$TARGET_R" --bars "$BARS" --benchmark "$BENCHMARK" \
  --instruments "$INSTRUMENTS" --db "$PAPER_DB" --asof "$ASOF"
```

Use the same global `--calendar` for both commands if overriding the bundled calendar. Review this evidence before reporting a paper pass.

If paper capacity changed since the earlier preview, preserve the original preview in the ledger and show the new terms before seeking confirmation.

- Instrument evidence must be same-day and available at the cutoff. Verify the NSE main-board cash equity series, ISIN, tick size, current price limits, surveillance and broker eligibility before attesting `clear`. Read restriction and operating-range sources for the specific instrument and date. [Exchange sources](../docs/india-market.md)
- The latest completed candle and fresh closed-bar signal must support the plan. Check entry drift, tick-adjusted levels, band distance, effective entry/flatten cutoffs and net reward/risk after costs.
- Use ledger cash, pending/open exposure, trade count, realized loss and committed risk. A prior-session unresolved position blocks new tickets until addressed in the ledger.

Return the exact block reason and evidence needed to resolve it, or report a pass for the specific preview with symbol, share count, effective limits, input timestamps, expiry and digest. Current venue and corporate-event context can change after the preview. Use `trader-news` for dated material events. An eligibility pass prepares a local paper preview. Confirmation runs its own fresh checks.
