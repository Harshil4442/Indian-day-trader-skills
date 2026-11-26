---
name: trader-news
description: Research Indian equity news and exchange announcements, verify intraday catalysts for an NSE watchlist, and check upcoming corporate or macro events using dated filings and optional broker headlines.
---

# Indian Equity News Monitor

## Collect the evidence

1. Identify the NSE symbols, the cutoff in IST and the requested lookback. For cross-listed companies, match announcements by issuer and ISIN so NSE and BSE versions of the same filing count once.
2. Start with exchange disclosures and attachments for results, board meeting notices, corporate actions, order wins, regulatory actions and halts. Use official RBI, MoSPI or Budget releases for market-wide events. Cite the underlying document and preserve the filing timestamp, event time and any amendments. [Indian market sources](../docs/india-market.md#official-filings-and-disclosures)
3. If authorised Upstox access is locally configured, look up headlines using the verified instrument key. Use each headline as a lead, then verify the substantive claim against the filing or issuer release. If the endpoint or a source is unavailable, report the missing coverage and use the available primary sources.
4. Use an existing event CSV or import the verified notices when a reusable record is needed. `published_at` is the first verifiable public time, `event_at` is the scheduled or observed time where known, and `*` identifies market-wide rows. Keep substantive revisions as separate filings with their own timestamps. [Events CSV contract](../docs/data-and-workflow.md#input-contract)

## Run from the checkout

Set `ASOF` to a timestamp with an explicit offset such as `2026-11-26T09:35:00+05:30` and use the user's event file. `--symbol` can be repeated for a watchlist.

```sh
python3 -m india_trader news --events state/events.csv --symbol RELIANCE \
  --asof "$ASOF" --hours 48 --forward-hours 24
python3 -m india_trader headlines --key 'NSE_EQ|INE002A01018' --asof "$ASOF" --page 1
```

`headlines` reads `UPSTOX_ACCESS_TOKEN` locally and filters the returned recent page to the cutoff. Use archived, timestamped records for a historical news review. `news` reports only the supplied records and includes upcoming events known at the cutoff.

## Report

- Show confirmed events with symbol, category, what changed, publication time in IST and the source link. Describe a possible price effect as an interpretation and label the evidence used for it.
- Call out scheduled event risk separately and show the release window when an exact time is unavailable. Mark unresolved headlines or rumors as unverified.
- State the watchlist, cutoff, sources checked and meaningful gaps. An empty result describes coverage of the checked sources.
