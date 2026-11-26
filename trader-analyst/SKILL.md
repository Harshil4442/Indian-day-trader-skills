---
name: trader-analyst
description: Analyse reported quarterly financial results for an Indian listed company, compare consolidated or standalone earnings year over year, and explain drivers, one-offs, restatements and data gaps from exchange filings.
---

# Indian Earnings Analyst

## Source and prepare the comparison

1. Identify the NSE symbol, the fiscal period, the results basis and the point-in-time cutoff. Use consolidated results when available unless the question concerns standalone operations. State the basis used.
2. Obtain the filed results and relevant notes or investor presentation. Use the exchange dissemination time and the document version available at the requested cutoff. Preserve later revisions with their own publication timestamps. [Indian filing sources](../docs/india-market.md#official-filings-and-disclosures)
3. Collect the current three-month quarter and the equivalent prior-year quarter in matching currency, basis and accounting definitions. Check group changes, fiscal-period shifts, disclosed restatements and split adjustments to per-share figures. Label any comparable series the issuer restated.
4. Use an existing results CSV or transcribe the sourced facts when a reusable record is needed. Enter three-month period ends, exact units, consistent metric labels and document links. Use the filing's published value and precision. [Results CSV contract](../docs/data-and-workflow.md#input-contract)

## Run from the checkout

Set `ASOF` to a timestamp with a UTC offset and point `--results` to the prepared input file.

```sh
python3 -m india_trader earnings RELIANCE --results state/earnings.csv \
  --basis consolidated --asof "$ASOF"
```

Use `--basis standalone` for a standalone comparison. The command selects the latest reported three-month quarter present in the input and the available prior-year period ending in the same month. It selects the latest supplied revision known at the cutoff, normalizes INR scalars to crore and leaves YoY percentage unavailable for percentage metrics or a nonpositive base.

## Explain what changed

- Show each comparable metric with periods, reported values, units and changes. For a margin or other percentage metric, use a percentage-point change. Explain absent comparables or losses turning into profits using the original values.
- For operating companies, prioritize reported revenue, profit, relevant margins, EPS and material exceptional items. For banks, consider net interest income, net interest margin, provisions and gross or net NPA ratios where disclosed.
- Attribute operating drivers, one-offs and forward guidance to specific filing notes or management statements. Keep any market reaction tied to a dated market-data observation and state the coverage of the results inputs.
