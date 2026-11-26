---
name: india-market-filings
description: Check Indian equity news, NSE or BSE issuer announcements, earnings, bulk and block deals, insider disclosures or other market events for a watchlist.
---

# India Market Filings

Use the [official source pointers](../../../docs/india-market.md) and [event and results schemas](../../../docs/data-and-workflow.md). From the checkout, `python3 -m india_trader news` reads dated event records and `headlines` optionally queries the broker’s read-only news endpoint. For a company announcement, use the exchange attachment and identify the affected symbol, reported event time and filing time in IST. Treat syndicated headlines as leads.

Use `earnings` to compare like-for-like reported quarters on the requested consolidated or standalone basis. Include the units, quarter and source. Label restatements, disclosed one-offs and differences in basis when they explain a change. Avoid guessing figures between filing dates.

Use `disclosures` for bulk and block deals, insider PIT and SAST notices, shareholding changes and pledges. Include the timestamp the public could see the filing and any reporting delay. Verify actual deal participants and terms from the filing when the question turns on them. Show upcoming events separately from events that happened. Report coverage as limited to the supplied sources and timestamps.
