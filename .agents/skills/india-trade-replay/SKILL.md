---
name: india-trade-replay
description: Replay India Day Trader setups on dated NSE cash-equity candles and estimate what happened in prior sessions, including costs, gaps and seeded scenarios.
---

# India Trade Replay

Use `python3 -m india_trader replay` from the checkout. Use `--help` and the [research workflow](../../../docs/data-and-workflow.md) to identify inputs. Keep the time cutoff, setup, quantity, tick, slippage, charges and benchmark consistent with the question. Supply sufficient earlier sessions for indicator warm-up. Use `--current` to add seeded path scenarios from a fresh signal at the requested `--asof`.

Read the historical coverage, filled and unfilled counts, unresolved paths, target and stop counts, time exits and net outcomes before reporting any rate. Separate prior actual candle paths from the resampled paths. Payments and fills are modeled. If the source history includes only surviving names, changed series, missing timestamps, inconsistent split adjustments or one volatility regime, mention the relevant limitation. A high draw count does not add independent observed sessions.

For a comparison, hold execution assumptions fixed and use a separate test period from the one used to choose thresholds. State the bootstrap seed and distinct matching dates. Use an isolated paper ledger to walk a particular scenario step by step.
