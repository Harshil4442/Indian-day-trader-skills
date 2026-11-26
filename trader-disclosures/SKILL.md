---
name: trader-disclosures
description: Review public Indian equity ownership and trade disclosures, including promoter or designated-person transactions, SEBI PIT and SAST disclosures, shareholding and pledges, and NSE or BSE bulk and block deals.
---

# Indian Trade and Ownership Disclosures

## Choose the source that answers the question

- Use NSE or BSE PIT filings for disclosed promoter, director or designated-person transactions. Use SAST filings for relevant acquisitions, disposals or encumbrances, and the exchange shareholding patterns for periodic ownership snapshots.
- Use the NSE or BSE bulk/block reports for reported exchange transactions. Confirm the venue, participant, buy or sell side, number of shares and disclosed price. Preserve partial information where the report lacks a counterparty or terms.
- For a named investor or promoter, verify that the filing identifies the exact person or entity before attributing a trade. Check transaction mode and distinguish a market trade, gift, internal transfer, pledge, release, invocation or other change. [Official disclosure sources](../docs/india-market.md#official-filings-and-disclosures)

## Review at an information cutoff

1. Capture the issuer, exchange, reported transaction or effective time, publication time in IST, document link and deal type. With a date alone, report lag at day-level precision. If two venues publish the same filing, retain the earliest verified dissemination and preserve subsequent material revisions.
2. Use an existing events CSV or import the verified disclosures for repeat use. The command selects `bulk-deal`, `block-deal`, `pit`, `sast`, `shareholding` and `pledge` rows. The CSV folds the filing description into `title` and links to terms in `url`. Verify any sizes, prices or ownership percentages directly against the filing. [Events CSV contract](../docs/data-and-workflow.md#input-contract)

Set `ASOF` to the review cutoff with a UTC offset. Repeat `--symbol` to include more issuers and set the publication lookback to cover the requested period.

```sh
python3 -m india_trader disclosures --events state/events.csv --symbol RELIANCE \
  --asof "$ASOF" --hours 168 --forward-hours 0
```

## Report the filing

- Show symbol, category, named participant where verified, action, terms, event date, filing timestamp, observed reporting delay and the primary document link. Label which date drives the search window.
- Explain what the filing establishes and where information is missing. Report a shareholding pattern as a dated snapshot and tie any inferred change to comparable earlier filings. A public disclosure can lag the underlying event and gives no reliable evidence of current intent or the next price move.
- State the exchanges, issuers, window and records covered. An empty result means no matching items were found in those inputs and sources reviewed.
