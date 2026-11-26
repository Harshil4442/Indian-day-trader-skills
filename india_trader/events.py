"""Point-in-time official announcements, disclosures and filed earnings imports."""

import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .data import csv_rows, upstox_get
from .models import IST, symbol_name, timestamp

EVENT_COLUMNS = ('symbol', 'published_at', 'event_at', 'kind', 'source', 'title', 'url')
RESULT_COLUMNS = ('symbol', 'published_at', 'period_end', 'period_months', 'basis', 'metric',
                  'value', 'unit', 'source_url')
DISCLOSURES = {'bulk-deal', 'block-deal', 'pit', 'sast', 'shareholding', 'pledge'}
MATERIAL_EVENTS = {'results', 'board-meeting', 'corporate-action', 'regulatory', 'halt', 'order-win',
                   'm-a', 'rbi-mpc', 'union-budget'}
UNITS = {'inr': 1e-7, 'inr-lakh': 0.01, 'inr-million': 0.1, 'inr-crore': 1.0}


def _url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f'expected a public HTTPS source link: {value!r}')
    return value


def events_at(path: Path, asof: datetime, *, symbols: set[str], hours: int = 48,
              forward_hours: int = 24, disclosures_only: bool = False) -> dict:
    if min(hours, forward_hours) < 0:
        raise ValueError('event windows must be nonnegative')
    earliest, latest_event = asof - timedelta(hours=hours), asof + timedelta(hours=forward_hours)
    seen = set()
    recent, upcoming = [], []
    for row in csv_rows(path, EVENT_COLUMNS):
        published = timestamp(row['published_at'])
        symbol = symbol_name(row['symbol']) if row['symbol'] != '*' else '*'
        kind = row['kind'].lower()
        if published > asof or (symbols and symbol != '*' and symbol not in symbols):
            continue
        if disclosures_only and kind not in DISCLOSURES:
            continue
        event_at = timestamp(row['event_at']) if row['event_at'] else None
        url = _url(row['url'])
        if not row['title'] or not row['source'] or not kind:
            raise ValueError('events require a title, kind and source')
        key = symbol, kind, published, event_at, url, row['title']
        if key in seen:
            continue
        seen.add(key)
        item = {'symbol': symbol, 'kind': kind, 'published_at': published, 'event_at': event_at,
                'source': row['source'], 'title': row['title'], 'url': url,
                'material_event_tag': kind in MATERIAL_EVENTS,
                'filing_lag_hours': ((published - event_at).total_seconds() / 3600
                                     if event_at and event_at <= published else None)}
        if event_at and asof < event_at <= latest_event:
            upcoming.append(item)
        elif published >= earliest and (event_at is None or event_at <= asof):
            recent.append(item)
    return {'input': str(path), 'asof': asof,
            'recent': sorted(recent, key=lambda item: item['published_at'], reverse=True),
            'upcoming': sorted(upcoming, key=lambda item: item['event_at']),
            'coverage': 'uploaded records only', 'event_hours': hours, 'forward_hours': forward_hours}


def earnings_at(path: Path, symbol: str, asof: datetime, basis: str) -> dict:
    facts = {}
    for row in csv_rows(path, RESULT_COLUMNS):
        published = timestamp(row['published_at'])
        if symbol_name(row['symbol']) != symbol or published > asof or row['basis'] != basis:
            continue
        period = date.fromisoformat(row['period_end'])
        if period > published.date() or int(row['period_months']) != 3:
            continue
        unit = row['unit'].lower()
        if unit not in {*UNITS, 'inr-per-share', 'pct'}:
            raise ValueError(f'unsupported earnings unit {unit!r}')
        value = float(row['value']) * UNITS.get(unit, 1)
        if not math.isfinite(value):
            raise ValueError('earnings value must be finite')
        metric = row['metric'].lower()
        if not metric:
            raise ValueError('an earnings metric label is required')
        item = {'value': value, 'unit': 'inr-crore' if unit in UNITS else unit,
                'published_at': published, 'source_url': _url(row['source_url'])}
        periods = facts.setdefault(metric, {})
        if period in periods and periods[period]['published_at'] == published and periods[period] != item:
            raise ValueError(f'conflicting filed values for {symbol} {metric} {period} at {published}')
        if period not in periods or periods[period]['published_at'] < published:
            periods[period] = item
    metrics = {}
    if facts:
        latest_period = max(period for points in facts.values() for period in points)
        for metric, points in sorted(facts.items()):
            current = points.get(latest_period)
            if current is None:
                continue
            previous_year = [period for period in points if period.year == latest_period.year - 1
                             and period.month == latest_period.month]
            previous_period = max(previous_year, default=None)
            prior = points.get(previous_period)
            comparable = prior and current['unit'] == prior['unit']
            difference = current['value'] - prior['value'] if comparable else None
            yoy_pct = (100 * difference / prior['value'] if comparable and prior['value'] > 0
                       and current['unit'] != 'pct' else None)
            metrics[metric] = {'period_end': latest_period, 'current': current,
                               'prior_year_period_end': previous_period, 'prior_year': prior,
                               'change_in_unit': difference, 'yoy_pct': yoy_pct}
    return {'symbol': symbol, 'asof': asof, 'basis': basis, 'metrics': metrics,
            'coverage': 'three-month filed facts supplied in CSV, as known at the requested time'}


def fetch_headlines(keys: list[str], token: str, asof: datetime, *, page: int = 1, opener=None) -> dict:
    keys = list(dict.fromkeys(keys))
    if not 1 <= len(keys) <= 30 or any(not re.fullmatch(r'NSE_EQ\|IN[A-Z0-9]{10}', key) for key in keys):
        raise ValueError('headlines expects 1 to 30 Upstox NSE_EQ keys')
    if not 1 <= page <= 100:
        raise ValueError('news page must be between 1 and 100')
    params = urlencode({'category': 'instrument_keys', 'instrument_keys': ','.join(keys), 'page_number': page})
    payload = upstox_get('/v2/news?' + params, token, opener=opener)
    try:
        by_key = payload['data']
        results = []
        for key in keys:
            for item in by_key.get(key, []):
                published = datetime.fromtimestamp(item['published_time'] / 1000, IST)
                if published <= asof:
                    results.append({'instrument_key': key, 'title': item['heading'],
                                    'published_at': published, 'url': _url(item['article_link'])})
    except (AttributeError, TypeError, KeyError, OverflowError) as exc:
        raise ValueError('invalid Upstox news response') from exc
    return {'source': 'Upstox News API', 'coverage': 'recent headlines for the requested keys and page',
            'asof': asof, 'page': payload.get('metadata', {}).get('page') or {'page_number': page},
            'headlines': sorted(results, key=lambda item: item['published_at'], reverse=True)}
