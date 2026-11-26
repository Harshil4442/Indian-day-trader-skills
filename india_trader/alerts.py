"""One-shot paper research alerts evaluated on completed candles and IST clocks."""

import json
import sqlite3
from contextlib import closing
from datetime import timedelta

from .data import price
from .models import FIVE_MINUTES, symbol_name, timestamp


def check_alerts(rules_path, db_path, by_symbol, calendar, asof):
    rules = json.loads(rules_path.read_text(encoding='utf-8'))
    if not isinstance(rules, list):
        raise ValueError('alerts file must contain a list of rules')
    matches, unseen, missing, ids = [], [], [], set()
    for rule in rules:
        key, kind = rule['id'], rule['kind']
        start = timestamp(rule['start_at'])
        if not isinstance(key, str) or not key.strip() or key in ids:
            raise ValueError('alert IDs must be unique nonempty strings')
        ids.add(key)
        calendar.require_coverage(start.date())
        if kind == 'clock':
            if start <= asof:
                matches.append({'id': key, 'kind': kind, 'observed_at': start})
            continue
        if kind not in {'touch-below', 'touch-above'}:
            raise ValueError(f'unsupported alert kind {kind!r}')
        level = price(rule['level'])
        symbol = symbol_name(rule['symbol'])
        session = calendar.sessions.get(start.date())
        if session is None:
            raise ValueError(f'price alert {key!r} has no regular session on {start.date()}')
        if symbol not in by_symbol:
            missing.append(symbol)
        for bar in by_symbol.get(symbol, ()):
            end = bar.ts + FIVE_MINUTES
            if bar.ts < start or end > asof or not session.contains_bar(bar.ts) or bar.volume <= 0:
                continue
            touched = bar.low <= level if kind == 'touch-below' else bar.high >= level
            if touched:
                matches.append({'id': key, 'kind': kind, 'observed_at': end, 'level': level,
                                'symbol': symbol, 'bar_start': bar.ts})
                break
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS fired (id TEXT PRIMARY KEY, observed_at TEXT NOT NULL)')
        for event in sorted(matches, key=lambda item: item['observed_at']):
            result = db.execute('INSERT OR IGNORE INTO fired VALUES (?, ?)',
                                (event['id'], event['observed_at'].isoformat()))
            if result.rowcount:
                unseen.append(event)
    return {'asof': asof, 'new_alerts': unseen, 'missing_symbols': sorted(set(missing))}
