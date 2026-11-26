---
name: trader-broker
description: Confirm an exact NSE cash-equity paper ticket or advance, inspect, cancel and reconcile a local India Day Trader simulated order.
---

# Trader Broker

Run from the checkout with the same risk config, applicable calendar and SQLite ledger used for the preview. This workflow acts on simulated orders in that local file. [Paper workflow](../docs/data-and-workflow.md)

Show the user the exact `ticket` preview, including symbol, shares, maximum modeled entry, stop, target, modeled stop loss, expiry and full digest. After the user explicitly approves that digest, use the latest completed candles and same-day instrument evidence. Refresh the preview and obtain approval again if its terms change or it expires.

```sh
trade() { python3 -m india_trader --config "$CONFIG" "$@"; }

trade paper-confirm "$SYMBOL" --digest "$DIGEST" --bars "$BARS" \
  --instruments "$INSTRUMENTS" --db "$PAPER_DB" --asof "$CONFIRM_ASOF"
trade paper-advance "$SYMBOL" --bars "$UPDATED_BARS" \
  --db "$PAPER_DB" --asof "$CHECK_ASOF"
trade paper-status --db "$PAPER_DB" --asof "$CHECK_ASOF"
```

On first confirmation, report `PENDING` as queued for the first eligible candle. On subsequent checks, report the observed ledger status, prices, times, charges and net P&L when available. The simulator processes complete candles in order. Gaps or a locked band can delay or prevent a simulated exit. Preserve unresolved exposure and its ledger reason.

Confirmation retries for the same digest return the existing order. Use the advancing IST clock for subsequent observations.

For an authorized cancellation of a pending entry, use `paper-cancel --digest "$DIGEST" --db "$PAPER_DB" --asof "$CHECK_ASOF"`. For a user-supplied, verifiable same-session exit on an unresolved open simulation, use `paper-reconcile --digest "$DIGEST" --db "$PAPER_DB" --asof "$OBSERVED_EXIT_AT" --exit-price "$OBSERVED_EXIT_PRICE" --note "$EVIDENCE_NOTE"`. Pass the observed exit time with its offset. Read back the ledger after either action.
