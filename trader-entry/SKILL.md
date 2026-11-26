---
name: trader-entry
description: Check whether a selected NSE cash equity has just confirmed an opening-range breakout, VWAP reclaim or trend pullback, and explain waiting, stale or blocked entry signals.
---

# NSE entry confirmation

Run from the India Day Trader checkout. Set `SYMBOL` to the NSE symbol, `BARS` to its five-minute candle CSV, and `ASOF` to an offset-bearing ISO 8601 cutoff. Supply matching Nifty 50 or sector candles if using a benchmark. Repeat `--bars` when the candles are in separate files. [Candle and calendar formats](../docs/data-and-workflow.md)

```sh
python3 -m india_trader calendar --asof "$ASOF"
python3 -m india_trader entry "$SYMBOL" --bars "$BARS" --benchmark NIFTY50 --asof "$ASOF"
```

Add `--config` before `entry` when the user has a specific setup policy. An explicitly supplied benchmark must have every completed slot needed by the equity check, even if a benchmark is optional in the configuration.

- Candle timestamps are start times. With a 09:15 IST regular open, the default range covers the 09:15, 09:20 and 09:25 candles. The earliest confirmation comes from the 09:30 candle when it closes at 09:35. Use the session returned by `calendar` for holidays and exceptions.
- `orb` confirms a close crossing above the opening-range high plus any configured buffer. `vwap-reclaim` needs a prior close at or below VWAP followed by a close above VWAP and the prior high. `trend-pullback` requires an impulse above the range and a resumption through the highs of a pullback supported by rising VWAP.
- Read the selected setup in `setups` and use `signals` to identify fresh confirmations. Report the confirmed close time, reference close in INR, trigger level, and important filters. The signal reference becomes an input to an exit plan. A later simulation determines any fill.
- `already_triggered` carries the earlier qualifying confirmation. For `waiting`, say which pattern or completed candle is pending. For `blocked`, state the reason codes, including data gaps, liquidity, volume or benchmark failures. `closed` and `expired` follow the current calendar and entry window.
- If the user asks for repeated checks, refresh the authorized data and re-run after each relevant candle close within the entry window. A repeated check on the same candle yields the same observation.

Check dated issuer and macro events before interpreting a fresh match. Use `trader-exit` to translate the structural level into a stop and target. [Indian market reference](../docs/india-market.md)

Copy the setup and cutoff into `plan` and any later replay so each step refers to the same confirmation.
