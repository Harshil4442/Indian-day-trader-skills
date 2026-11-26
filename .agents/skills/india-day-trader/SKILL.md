---
name: india-day-trader
description: Screen NSE cash equities, assess a closed-bar intraday setup, plan its structural exit, or check IST market and price alerts with India Day Trader.
---

# India Day Trader

Run `python3 -m india_trader` from the India Day Trader checkout, or use the installed `india-trader` command. Use `--help` on a subcommand to get its exact arguments. The [data contract](../../../docs/data-and-workflow.md) explains inputs and time alignment. Use the [Indian market reference](../../../docs/india-market.md) for exchange rules and source lookups.

- Identify the NSE symbol or dated universe, candle source, time cutoff and benchmark. Use `calendar` for the date and `universe` for the watchlist. When the user wants a tour without data, generate synthetic inputs with `demo` and follow the README.
- Use `premarket` for the prior modeled sessions and `scan` for intraday matches. Report observed coverage, market and stock returns from aligned bars, cumulative and trigger-bar relative volume, range location and the missing inputs that matter. A scan can be partial. A benchmark can be Nifty 50 or a relevant sector index.
- Use `entry` to distinguish a newly confirmed setup from one triggered earlier in the session, a forming candle, or a blocked setup. Use `plan` to produce a structural stop and target from a fresh confirmation. Keep signal closes, trigger levels and potential fill prices distinct.
- Build and check `alerts` rules when requested. Tell the user the candle close or IST clock when a condition was observed. Refresh the candles at each check.

For a replay, use `india-trade-replay`. For a sized or confirmed local paper ticket, use `india-paper-trader`. For dated corporate context, use `india-market-filings`. A rule match is a research result. Show the exact cutoff and material caveats when evidence is incomplete.
