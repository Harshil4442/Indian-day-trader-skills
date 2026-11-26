"""Generate clearly labeled synthetic data for the documented offline walkthrough."""

import csv
import json
from datetime import date

from .data import INSTRUMENT_COLUMNS, write_bars
from .events import EVENT_COLUMNS, RESULT_COLUMNS
from .models import Bar, FIVE_MINUTES, load_calendar

DEMO_DAY = date(2026, 11, 26)
SOURCE = 'synthetic demo, no real market data'


def _write_csv(path, columns, rows):
    with path.open('w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(columns)
        writer.writerows(rows)


def _candles(session, offset, breakout):
    for slot in range((session.closes - session.opens) // FIVE_MINUTES):
        stamp = session.opens + slot * FIVE_MINUTES
        if slot < 3:
            o, h, l, c, v = [(500, 501.5, 498, 500.5, 18_000), (500.5, 502.5, 499, 502, 19_000),
                             (502, 503, 500.5, 501.5, 17_000)][slot]
        elif slot == 3 and breakout:
            o, h, l, c, v = 501.5, 505.5, 501, 505, 48_000
        elif slot == 4 and breakout:
            o, h, l, c, v = 505, 507.5, 504.5, 507, 23_000
        elif slot == 5 and breakout:
            if session.day != DEMO_DAY and offset % 3 == 1:
                o, h, l, c, v = 507, 510, 499, 501, 26_000
            else:
                o, h, l, c, v = 507, 518, 506, 516, 26_000
        else:
            o, h, l, c, v = 500, 502, 498.5, 500 + (slot % 4) / 4, 18_000
        yield Bar('DEMO', stamp, o, h, l, c, v)
        yield Bar('NIFTY50', stamp, 25_000, 25_014, 24_999, 25_008 + (slot % 4), 0)


def create_demo(output):
    output.mkdir(parents=True, exist_ok=True)
    sessions = [session for day, session in load_calendar().sessions.items() if day <= DEMO_DAY][-32:]
    candles = [bar for offset, session in enumerate(sessions)
               for bar in _candles(session, offset, session.day == DEMO_DAY or offset % 5 == 0)]
    write_bars(output / 'bars.csv', candles, SOURCE)
    _write_csv(output / 'universe.csv', ('Symbol', 'Industry', 'Series', 'ISIN Code'),
               [('DEMO', 'Synthetic sector', 'EQ', 'INE000D01001')])
    _write_csv(output / 'instruments.csv', INSTRUMENT_COLUMNS,
               [('DEMO', 'NSE', 'EQ', 'INE000D01001', 'Synthetic sector', 0.05, 450, 550, 'clear',
                 '2026-11-26T09:15:00+05:30', SOURCE, 'NSE_EQ|INE000D01001')])
    _write_csv(output / 'events.csv', EVENT_COLUMNS, [
        ('DEMO', '2026-11-26T08:45:00+05:30', '', 'order-win', SOURCE,
         'Synthetic company exchange announcement', 'https://example.com/synthetic-announcement'),
        ('DEMO', '2026-11-25T17:30:00+05:30', '2026-11-25T14:30:00+05:30', 'bulk-deal', SOURCE,
         'Synthetic bulk deal filing', 'https://example.com/synthetic-bulk-deal'),
        ('DEMO', '2026-11-23T08:50:00+05:30', '2026-11-26T16:00:00+05:30', 'board-meeting', SOURCE,
         'Synthetic board meeting notice', 'https://example.com/synthetic-board-meeting'),
    ])
    _write_csv(output / 'earnings.csv', RESULT_COLUMNS, [
        ('DEMO', '2026-11-03T16:15:00+05:30', '2026-09-30', 3, 'consolidated', 'revenue',
         126, 'inr-crore', 'https://example.com/synthetic-results-2026'),
        ('DEMO', '2025-11-04T16:20:00+05:30', '2025-09-30', 3, 'consolidated', 'revenue',
         110, 'inr-crore', 'https://example.com/synthetic-results-2025'),
    ])
    (output / 'alerts.json').write_text(json.dumps([
        {'id': 'demo-target', 'symbol': 'DEMO', 'kind': 'touch-above', 'level': 516,
         'start_at': '2026-11-26T09:35:00+05:30'},
        {'id': 'demo-flat', 'kind': 'clock', 'start_at': '2026-11-26T15:05:00+05:30'},
    ], indent=2) + '\n', encoding='utf-8')
    return {'output': str(output), 'data': SOURCE, 'modeled_sessions': len(sessions),
            'signal_asof': '2026-11-26T09:35:00+05:30', 'advance_asof': '2026-11-26T09:45:00+05:30'}
