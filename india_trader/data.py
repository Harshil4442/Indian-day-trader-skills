"""Validated CSV inputs and the read-only Upstox market-data client."""

import csv
import json
import math
import os
import re
import tempfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .models import Bar, FIVE_MINUTES, IST, Instrument, symbol_name, timestamp

BAR_COLUMNS = ('symbol', 'ts', 'open', 'high', 'low', 'close', 'volume')
INSTRUMENT_COLUMNS = ('symbol', 'exchange', 'series', 'isin', 'sector', 'tick_size',
                      'lower_band', 'upper_band', 'status', 'asof', 'source', 'instrument_key')


def csv_rows(path: Path, required: tuple[str, ...]):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or set(required) - set(fields):
            raise ValueError(f'{path} must have unique headers including {", ".join(required)}')
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'{path}:{line}: field count does not match header')
            try:
                yield {key: value.strip() for key, value in row.items()}
            except AttributeError as exc:
                raise ValueError(f'{path}:{line}: invalid CSV field') from exc


def price(value) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f'price must be positive and finite: {value!r}')
    return result


def _bar(symbol, ts, open_, high, low, close, volume) -> Bar:
    o, h, l, c = map(price, (open_, high, low, close))
    if l > min(o, c) or h < max(o, c):
        raise ValueError(f'inconsistent OHLC for {symbol} at {ts}')
    if isinstance(volume, bool) or not re.fullmatch(r'[0-9]+', str(volume)):
        raise ValueError(f'volume must be a nonnegative integer for {symbol} at {ts}')
    return Bar(symbol_name(symbol), timestamp(ts), o, h, l, c, int(volume))


def read_bars(paths: list[Path]) -> dict[str, list[Bar]]:
    by_symbol: dict[str, dict[datetime, Bar]] = defaultdict(dict)
    for path in paths:
        try:
            for row in csv_rows(path, BAR_COLUMNS):
                bar = _bar(*(row[field] for field in BAR_COLUMNS))
                existing = by_symbol[bar.symbol].setdefault(bar.ts, bar)
                if existing != bar:
                    raise ValueError(f'conflicting candles for {bar.symbol} at {bar.ts.isoformat()}')
        except ValueError as exc:
            raise ValueError(f'{path}: {exc}') from exc
    return {symbol: sorted(bars.values(), key=lambda bar: bar.ts) for symbol, bars in by_symbol.items()}


def instruments_at(path: Path, asof: datetime) -> dict[str, Instrument]:
    latest = {}
    snapshots = set()
    for row in csv_rows(path, INSTRUMENT_COLUMNS):
        symbol = symbol_name(row['symbol'])
        observed = timestamp(row['asof']) if row['asof'] else None
        key = symbol, observed
        if key in snapshots:
            raise ValueError(f'{path}: duplicate instrument snapshot for {symbol} at {observed}')
        snapshots.add(key)
        if row['exchange'].upper() != 'NSE':
            raise ValueError(f'{path}: only NSE instrument snapshots are supported: {symbol}')
        if observed and observed > asof:
            continue
        previous = latest.get(symbol)
        if previous and (not observed or (previous.asof and previous.asof > observed)):
            continue
        tick, lower, upper = (price(row[field]) if row[field] else None
                              for field in ('tick_size', 'lower_band', 'upper_band'))
        if lower and upper and lower >= upper:
            raise ValueError(f'{path}: lower band must be below upper band for {symbol}')
        isin = row['isin'].upper()
        if isin and not re.fullmatch(r'IN[A-Z0-9]{10}', isin):
            raise ValueError(f'{path}: invalid Indian ISIN for {symbol}')
        instrument_key = row['instrument_key']
        if instrument_key and (not isin or instrument_key != f'NSE_EQ|{isin}'):
            raise ValueError(f'{path}: NSE_EQ instrument key must match ISIN for {symbol}')
        latest[symbol] = Instrument(symbol, 'NSE', row['series'].upper(), isin, row['sector'],
                                    tick, lower, upper, row['status'].lower() or 'unknown',
                                    observed, row['source'], instrument_key)
    return latest


def read_universe(path: Path) -> dict[str, dict]:
    """Reads an NSE Indices constituent export or a one-column watchlist."""
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        headers = {field.strip().lower(): field for field in reader.fieldnames or []}
        if 'symbol' not in headers:
            raise ValueError(f'{path}: expected an NSE Indices CSV or one-column symbol CSV')
        universe = {}
        for row in reader:
            symbol = symbol_name(row[headers['symbol']] or '')
            if symbol in universe:
                raise ValueError(f'{path}: duplicate universe symbol {symbol}')
            series = (row.get(headers.get('series', '')) or 'EQ').strip().upper()
            if series == 'EQ':
                universe[symbol] = {'symbol': symbol, 'series': series,
                                    'sector': (row.get(headers.get('industry', '')) or '').strip(),
                                    'isin': (row.get(headers.get('isin code', '')) or '').strip()}
        return universe


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


def upstox_get(path: str, token: str, *, opener=None) -> dict:
    if not token or '\n' in token or '\r' in token:
        raise ValueError('set UPSTOX_ACCESS_TOKEN to an authorized access token')
    request = Request('https://api.upstox.com' + path,
                      headers={'Accept': 'application/json', 'Authorization': f'Bearer {token}'})
    open_response = opener or build_opener(_NoRedirect()).open
    try:
        with open_response(request, timeout=15) as response:
            raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise ValueError('Upstox response exceeds the expected size')
            payload = json.loads(raw)
    except HTTPError as exc:
        raise ValueError(f'Upstox returned HTTP {exc.code}; check access and rate limits') from None
    except URLError as exc:
        raise ValueError('Upstox connection failed') from exc
    if not isinstance(payload, dict) or payload.get('status') != 'success':
        raise ValueError('Upstox did not return a successful response')
    return payload


def fetch_upstox(symbol: str, instrument_key: str, token: str, start: date | None = None,
                  end: date | None = None, *, opener=None, requested_at: datetime | None = None) -> list[Bar]:
    """Fetch one five-minute NSE cash or index chart through a documented GET endpoint."""
    if not re.fullmatch(r'NSE_(?:EQ\|IN[A-Z0-9]{10}|INDEX\|[A-Za-z0-9 &_.+()-]{1,50})', instrument_key):
        raise ValueError('expected an Upstox NSE_EQ ISIN key or an NSE_INDEX key')
    if (start is None) != (end is None):
        raise ValueError('provide both --from-date and --to-date for historical candles')
    if start is not None and (end < start or (end - start).days > 30):
        raise ValueError('five-minute historical requests must span at most 31 calendar days')
    base = '/v3/historical-candle'
    key = quote(instrument_key, safe='')
    path = f'{base}/{key}/minutes/5/{end}/{start}' if start else f'{base}/intraday/{key}/minutes/5'
    requested_at = requested_at or datetime.now(IST)
    payload = upstox_get(path, token, opener=opener)
    try:
        if not isinstance(payload['data']['candles'], list):
            raise ValueError('Upstox did not return a successful candle response')
        bars = {}
        for row in payload['data']['candles']:
            bar = _bar(symbol, *row[:6])
            if start and not start <= bar.ts.date() <= end:
                raise ValueError('Upstox returned a candle outside the requested date range')
            if bar.ts + FIVE_MINUTES > requested_at:
                continue
            existing = bars.setdefault(bar.ts, bar)
            if existing != bar:
                raise ValueError(f'conflicting Upstox candles at {bar.ts.isoformat()}')
    except (TypeError, KeyError) as exc:
        raise ValueError('invalid Upstox candle response') from exc
    if not bars:
        raise ValueError(f'Upstox returned no completed candles for {symbol}')
    return sorted(bars.values(), key=lambda bar: bar.ts)


def write_bars(path: Path, bars: list[Bar], source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='', dir=path.parent,
                                     delete=False) as stream:
        temporary = Path(stream.name)
        try:
            writer = csv.writer(stream)
            writer.writerow((*BAR_COLUMNS, 'source'))
            for bar in bars:
                writer.writerow((bar.symbol, bar.ts.isoformat(), bar.open, bar.high, bar.low,
                                 bar.close, bar.volume, source))
            stream.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
