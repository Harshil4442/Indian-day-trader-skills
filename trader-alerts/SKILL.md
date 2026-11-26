---
name: trader-alerts
description: Check one-shot NSE cash-equity price touches and IST clock reminders from refreshed five-minute bars and local alert rules.
---

# Trader Alerts

Use this skill when the user asks to watch a price level, planned stop or target, or an IST deadline. Check [the data contract](../docs/data-and-workflow.md) for candle and alert inputs. Run commands from the India Day Trader checkout.

Create one rule per condition in a JSON array. Use the verified NSE symbol, a unique rule ID and a timestamp with an explicit UTC offset. Use the modeled ticket's own `flatten_at` for a reminder tied to a paper position.

```json
[
  {
    "id": "demo-target-2026-11-26",
    "kind": "touch-above",
    "symbol": "DEMO",
    "level": 516.25,
    "start_at": "2026-11-26T09:35:00+05:30"
  },
  {
    "id": "demo-flat-2026-11-26",
    "kind": "clock",
    "start_at": "2026-11-26T15:05:00+05:30"
  }
]
```

Use `touch-below` for a downside level or `touch-above` for an upside level. A price rule scans regular-session bars from its start time on that date. Set `start_at` on the relevant five-minute boundary. On each check, refresh candles and reuse the same alert database to preserve one-shot delivery.

For a quick test, generate the offline demo and check its supplied rules.

```sh
python3 -m india_trader demo
python3 -m india_trader alerts --bars state/demo/bars.csv \
  --rules state/demo/alerts.json --db state/demo/alerts.sqlite3 \
  --asof 2026-11-26T09:45:00+05:30
```

Report `new_alerts` with symbol, level and `observed_at` in IST. For a level touch, `observed_at` is the end of the completed candle. Report missing symbols and identify when the latest usable candle is stale. An empty result says no new alert was recorded from the supplied inputs. A candle touch provides no fill evidence. Use `trader-broker` to inspect simulated execution and exposure.
