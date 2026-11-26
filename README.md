# India Day Trader

India Day Trader is a local Python toolkit with 11 standalone agent skills for NSE cash-equity intraday research and paper trading. It follows the research-to-ticket workflow of Jake Schincariol’s US [Day Trader skill](https://github.com/Jakeschincariol/day-trader-skill), with Indian exchange sessions, instruments, disclosures and INR costs.

- **Scan** a watchlist or an NSE Indices constituent export. Research opening-range breakouts, VWAP reclaims and trend pullbacks on completed five-minute candles. Inspect aligned Nifty or sector-relative returns, elapsed-session relative volume, liquidity, gap size and breadth coverage.
- **Replay** earlier occurrences using point-in-time inputs, modeled fills and fees. Resample complete historical price paths for a fresh plan and see how many distinct sessions support the results.
- **Prepare and paper trade** a sized entry, stop and target. Review one exact ticket, then confirm its digest to queue a simulated fill. The ledger records open risk, realized P&L, cancellations and unresolved exits.
- **Review market context** with news headlines, exchange announcements, earnings, bulk/block deals and insider disclosures. Local alerts detect level touches and IST deadlines.

The initial scope is long-only, unleveraged NSE main-board cash equities. The only broker integration reads market data. Orders and positions live in a local SQLite paper ledger. The included data and walk-through are synthetic. Parameters are research defaults awaiting validation on representative Indian market history.

## The 11 skills

Each folder is visible at the repository root and contains its own `SKILL.md`. The skills share the Python toolkit under `india_trader/`.

| Skill | Workflow |
| --- | --- |
| [`trader-scanner`](trader-scanner/SKILL.md) | Review daily setups and scan an NSE watchlist for intraday candidates |
| [`trader-news`](trader-news/SKILL.md) | Check headlines, exchange announcements and upcoming market events |
| [`trader-entry`](trader-entry/SKILL.md) | Check whether a setup has confirmed on a completed five-minute candle |
| [`trader-exit`](trader-exit/SKILL.md) | Set a structural stop, target and modeled square-off time |
| [`trader-timemachine`](trader-timemachine/SKILL.md) | Replay prior signals and run seeded scenarios for a fresh plan |
| [`trader-budget`](trader-budget/SKILL.md) | Size an INR paper ticket after charges, cash and allocation limits |
| [`trader-risk`](trader-risk/SKILL.md) | Check eligibility, price bands, exposure, loss limits and ticket expiry |
| [`trader-broker`](trader-broker/SKILL.md) | Confirm, advance, cancel and reconcile exact local paper tickets |
| [`trader-alerts`](trader-alerts/SKILL.md) | Check one-shot price touches and IST clock reminders |
| [`trader-analyst`](trader-analyst/SKILL.md) | Compare source-linked NSE or BSE quarterly results |
| [`trader-disclosures`](trader-disclosures/SKILL.md) | Review bulk and block deals, PIT/SAST filings, shareholding and pledges |

`trader-disclosures` is the Indian counterpart to the US pack’s `trader-pelosi`. [Upstream workflow mapping](docs/upstream-review.md)

## Run the offline walkthrough

Requires Python 3.11 or newer. From the repository root, use `python3 -m india_trader`. An optional `python3 -m pip install -e .` adds the `india-trader` shell command. The walkthrough uses `mktemp` and POSIX shell syntax.

```sh
DEMO_DIR=$(mktemp -d)
ASOF=2026-11-26T09:35:00+05:30
python3 -m india_trader demo --output "$DEMO_DIR"

trade() { python3 -m india_trader --config examples/config.json "$@"; }
trade scan --bars "$DEMO_DIR/bars.csv" --universe "$DEMO_DIR/universe.csv" \
  --benchmark NIFTY50 --asof "$ASOF"
trade news --events "$DEMO_DIR/events.csv" --symbol DEMO --asof "$ASOF"
trade plan DEMO --bars "$DEMO_DIR/bars.csv" --benchmark NIFTY50 --asof "$ASOF" --target-r 2.5
trade replay DEMO --bars "$DEMO_DIR/bars.csv" --benchmark NIFTY50 --asof "$ASOF" \
  --current --target-r 2.5 --quantity 49 --simulations 1000
trade ticket DEMO --bars "$DEMO_DIR/bars.csv" --instruments "$DEMO_DIR/instruments.csv" \
  --benchmark NIFTY50 --db "$DEMO_DIR/paper.sqlite3" --asof "$ASOF" --target-r 2.5
```

The 09:30 candle closes at 09:35 and confirms an ORB at ₹505. The 2.5R plan previews 49 synthetic shares, a ₹500.50 stop and a ₹516.25 target. Review the ticket and copy its full `digest` for the next command. The demo holds the confirmation clock at the signal boundary so the 09:35 candle is the first eligible fill candle.

```sh
DIGEST='paste the exact ticket digest here'
trade paper-confirm DEMO --digest "$DIGEST" --bars "$DEMO_DIR/bars.csv" \
  --instruments "$DEMO_DIR/instruments.csv" --db "$DEMO_DIR/paper.sqlite3" --asof "$ASOF"
trade paper-advance DEMO --bars "$DEMO_DIR/bars.csv" --db "$DEMO_DIR/paper.sqlite3" \
  --asof 2026-11-26T09:45:00+05:30
trade paper-status --db "$DEMO_DIR/paper.sqlite3" --asof 2026-11-26T09:45:00+05:30
trade alerts --bars "$DEMO_DIR/bars.csv" --rules "$DEMO_DIR/alerts.json" \
  --db "$DEMO_DIR/alerts.sqlite3" --asof 2026-11-26T09:45:00+05:30
```

The simulation should close the position at the target and emit one price alert. `paper-status` is the current ledger with a trading-day filter. Use `replay` or a separate ledger for alternate historical scenarios. Run `python3 -m india_trader --help` for all commands.

## Connect your research data

1. Download a dated [Nifty 100 constituent list](https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-100) or make a CSV with a `symbol` header. Use this as the scan universe, then check each candidate against current NSE instrument and surveillance records.
2. Supply five-minute candles from a provider you’re authorised to use. Pass `--bars` for each file. The included Upstox adapter reads the historical or current-session endpoint and uses instrument keys from the broker directory. Store the token locally in `UPSTOX_ACCESS_TOKEN`. [Upstox authentication](https://upstox.com/developer/api-documentation/authentication/) · [Instrument directory](https://upstox.com/developer/api-documentation/instruments/) · [Historical candles](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/)
3. Supply dated instrument snapshots for tickets and filed event/result records for official context. Headlines from the optional Upstox `headlines` command are leads to verify against filings. Replace the sample risk limits and charges with the amounts and fee assumptions for your paper account. [Data formats and operational workflow](docs/data-and-workflow.md) · [Upstox News API](https://upstox.com/developer/api-documentation/get-news/)

```sh
# Use the broker's current key for the equity, verified against its ISIN.
python3 -m india_trader fetch-bars RELIANCE --key 'NSE_EQ|INE002A01018' \
  --from-date 2026-11-01 --to-date 2026-11-25 --output state/reliance-history.csv
python3 -m india_trader fetch-bars RELIANCE --key 'NSE_EQ|INE002A01018' \
  --output state/reliance-current.csv
python3 -m india_trader fetch-bars NIFTY50 --key 'NSE_INDEX|Nifty 50' --output state/nifty-current.csv
```

Fetch prior sessions for the benchmark as well when running replay. The included calendar covers 2026 and excludes auction candles. It uses the shared conservative 15:15 continuous cutoff from 3 August through 6 December, then the published 15:30 session from 7 December. Pass `--calendar` before the command to use an updated or historical file. [NSE closing-auction rules](https://www.nseindia.com/static/products-services/closing-auction-session) · [NSE December timetable](https://nsearchives.nseindia.com/web/mediaattachment/2026-11/FAQs_for_CM_and_FO_FOR_CAS_2.0_20261109152618.pdf)

## Use the agent skills

In Codex, open this checkout and select the workflow from the `$` skill picker. Look for `trader-scanner` or `indian-day-trader-skills:trader-scanner`, depending on how the client loads the pack. The `.agents/skills/` directory links each entry to its top-level skill folder.

In Claude Code, start from this checkout. Load the pack for the current session with `claude --plugin-dir .`, or add it as a marketplace and install it in the project:

```text
/plugin marketplace add Harshil4442/Indian-day-trader-skills
/plugin install indian-day-trader-skills@indian-day-trader-skills
```

Invoke a plugin skill such as `/indian-day-trader-skills:trader-scanner`. The workflows run the shared Python toolkit from the checkout.

See the [upstream review and localization decisions](docs/upstream-review.md), the [Indian market reference](docs/india-market.md) and the [data contract](docs/data-and-workflow.md). Run the offline tests with `python3 -m unittest discover -s tests -v`.
