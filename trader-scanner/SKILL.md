---
name: trader-scanner
description: Build a dated NSE cash-equity watchlist, run a premarket technical screen, or scan multiple symbols for intraday opening-range breakouts, VWAP reclaims and trend pullbacks.
---

# NSE chart scanner

Run from the India Day Trader checkout. Set `UNIVERSE` to a dated NSE constituent CSV or a watchlist with a `symbol` header, `BARS` to a five-minute candle CSV, and `ASOF` to an ISO 8601 cutoff with a UTC offset. Repeat `--bars` for separate equity and index files. Use `--symbol` repeatedly to restrict the scan. [Data formats](../docs/data-and-workflow.md)

```sh
python3 -m india_trader universe "$UNIVERSE"
python3 -m india_trader premarket --universe "$UNIVERSE" --bars "$BARS" --asof "$ASOF"
python3 -m india_trader scan --universe "$UNIVERSE" --bars "$BARS" \
  --benchmark NIFTY50 --asof "$ASOF"
```

- Use `premarket` to review completed continuous sessions. Report modeled median daily INR turnover, ATR, Wilder RSI and the matching structures, such as NR7, inside session, 20-session high or average reclaim. An incomplete prior session can interrupt the daily history.
- Use `scan` for fresh closed-bar matches. Report the cutoff and latest bar close, requested and received charts, missing symbols, and breadth coverage. Breadth counts advancing, declining and unchanged names against their session opens within the supplied universe.
- For each candidate, show the setup and the relevant inputs from `metrics`, including range, VWAP, opening gap, cumulative and trigger-bar relative volume, matched prior sessions and returns relative to the supplied benchmark. Relative volume compares like elapsed slots. The gap uses the prior modeled continuous close.
- Use an aligned Nifty 50 or sector index for `--benchmark`. The provider has to supply the completed matching index slots. A missing slot blocks confirmation when a benchmark is supplied.
- `--instruments` attaches dated instrument evidence. Treat surveillance, series, price bands and broker eligibility as separate from chart matches. For a selected symbol, use `trader-entry` for the latest confirmation and `trader-news` for dated context. [NSE sessions, surveillance and sources](../docs/india-market.md)

The calendar packaged with the repo covers 2026. For other dates, pass an updated `--calendar` before the subcommand. Signal filters can be changed with `--config` before the subcommand. Report material parameter changes alongside results.

The standard scan uses a 15-minute opening range and at least five earlier sessions in a rolling 20-session baseline. Mark a partial scan as partial even if the available charts produce candidates.
