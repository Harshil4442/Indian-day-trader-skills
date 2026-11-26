---
name: trader-timemachine
description: Backtest the first qualifying NSE intraday signal on earlier sessions, or run seeded historical-path scenarios for a fresh India Day Trader plan with execution costs.
---

# NSE Time Machine

Run from the India Day Trader checkout. Set `SYMBOL`, a five-minute historical candle file `BARS`, and an ISO 8601 `ASOF` cutoff with a UTC offset. Add earlier candles for warm-up and aligned historical benchmark candles when using `--benchmark`. Repeat `--bars` for additional files. [Historical data contract](../docs/data-and-workflow.md)

```sh
python3 -m india_trader --config examples/config.json replay "$SYMBOL" --bars "$BARS" \
  --benchmark NIFTY50 --asof "$ASOF" --setup orb --target-r 2 --tick-size 0.05 --quantity 25
python3 -m india_trader --config examples/config.json replay "$SYMBOL" --bars "$BARS" \
  --benchmark NIFTY50 --asof "$ASOF" --setup orb --target-r 2 --tick-size 0.05 --quantity 25 \
  --current --simulations 1000 --seed 19
```

Use `orb`, `vwap-reclaim` or `trend-pullback`. The quantity and tick in the examples are illustrative. Use an evidence-based tick and a relevant quantity because costs affect net R. Review the configured fee, slippage, drift and flat-time assumptions. [NSE execution and fee sources](../docs/india-market.md)

- `history` examines earlier dated sessions with a rolling baseline available at each signal. It models entry at the following open within the drift limit. Stop gaps use the adverse open and an ambiguous stop/target candle takes the stop first. A missing or unobservable exit can leave a fill unresolved.
- Report `observations`, `resolved_fills`, `unresolved_fills` and relevant `outcome_counts` before the target, stop and flat fractions. The fractions use resolved fills as their denominator. Include mean or median net R after modeled fees and slippage when available.
- `--current` needs a fresh confirmation at `--asof` and adds a `bootstrap` report. It draws whole matched-time paths from prior complete sessions. Report the seed, draw count and `matched_sessions`. When `insufficient_matched_sessions` is returned, report coverage and omit simulated frequencies.
- Inferred fills use candle OHLC, fixed execution assumptions and the supplied instrument history. Disclose material gaps, point-in-time membership or event-data limitations, and compare threshold changes on a separate test period. Repeated bootstrap draws reuse the observed sessions.

The report describes historical rule behavior and scenario outcomes. For paper sizing or exact confirmation, use `trader-budget` or `trader-broker`.
