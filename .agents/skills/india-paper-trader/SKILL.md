---
name: india-paper-trader
description: Size and risk-check an INR NSE cash-equity paper ticket, confirm an exact digest, or inspect, advance, cancel and reconcile the local simulated trading ledger.
---

# India Paper Trader

Use `python3 -m india_trader` from the checkout. See the [data contract](../../../docs/data-and-workflow.md) for the candle and instrument snapshot fields. Keep the same `--db` and risk config across steps. The example budget is for demonstration. Use the user’s designated paper capital and limits when preparing a ticket for them.

1. Run `ticket` with a fresh setup, latest closed candles and the dated evidence that establishes the instrument’s series, eligibility, tick and price range. Show shares, entry reference and maximum modeled entry, stop, target, modeled rupee loss, estimated costs, net reward/risk, expiry and digest. If risk blocks it, report the reason and keep the ticket unconfirmed.
2. Use `paper-confirm` only after the user explicitly confirms that exact digest. Report a queued simulation. Reject a request to treat a general “yes” or a similar ticket as approval of the current digest.
3. Use `paper-advance` on subsequent completed candles to apply fills, and `paper-status` to read the current ledger state. A candle gap or locked exit can leave an open position. Use `paper-cancel` for a user-requested pending cancellation. Use `paper-reconcile` only with user-supplied dated exit evidence and an evidence note.

This skill controls the local paper ledger. Keep broker tokens outside chat and use the market-data connection only for read operations. [Broker and exchange context](../../../docs/india-market.md)
