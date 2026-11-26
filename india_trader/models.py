"""Shared market records and NSE session calendar."""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
FIVE_MINUTES = timedelta(minutes=5)
DEFAULT_CALENDAR = Path(__file__).with_name('calendars') / 'nse-cm-2026.json'


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f'timestamp needs a UTC offset: {value!r}')
    return parsed.astimezone(IST)


def symbol_name(value: str) -> str:
    normalized = value.strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9&_.-]{0,39}', normalized):
        raise ValueError(f'invalid symbol or index alias: {value!r}')
    return normalized


@dataclass(frozen=True)
class Bar:
    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class Session:
    day: date
    opens: datetime
    closes: datetime
    kind: str = 'regular'

    def contains_bar(self, start: datetime) -> bool:
        offset = start - self.opens
        return (self.opens <= start and start + FIVE_MINUTES <= self.closes
                and offset % FIVE_MINUTES == timedelta(0))


@dataclass(frozen=True)
class Instrument:
    symbol: str
    exchange: str = 'NSE'
    series: str = 'EQ'
    isin: str = ''
    sector: str = ''
    tick_size: float | None = None
    lower_band: float | None = None
    upper_band: float | None = None
    status: str = 'unknown'
    asof: datetime | None = None
    source: str = ''
    instrument_key: str = ''


@dataclass(frozen=True)
class Calendar:
    start: date
    end: date
    sessions: dict[date, Session]
    exceptions: dict[date, str]
    calendar_id: str

    def require_coverage(self, day: date) -> None:
        if not self.start <= day <= self.end:
            raise ValueError(f'{day} is outside {self.calendar_id} coverage ({self.start} to {self.end})')

    def status(self, day: date) -> str:
        self.require_coverage(day)
        if day in self.sessions:
            return 'regular'
        return self.exceptions.get(day, 'weekend' if day.weekday() >= 5 else 'closed')


def _session(day: date, hours: list[str]) -> Session:
    if len(hours) != 2 or any(not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value) for value in hours):
        raise ValueError(f"calendar session must specify two local HH:MM times: {day}")
    start, end = (datetime.combine(day, time.fromisoformat(v), IST) for v in hours)
    if (end <= start or start.second or end.second or start.microsecond or end.microsecond
            or (end - start) % FIVE_MINUTES):
        raise ValueError(f'calendar session must be a positive multiple of five minutes: {day}')
    return Session(day, start, end)


def load_calendar(path: Path = DEFAULT_CALENDAR) -> Calendar:
    spec = json.loads(path.read_text(encoding='utf-8'))
    start, end = date.fromisoformat(spec['start']), date.fromisoformat(spec['end'])
    if start > end or (end - start).days > 3660:
        raise ValueError('calendar coverage is invalid')
    holidays = {date.fromisoformat(key): value for key, value in spec['holidays'].items()}
    exceptions = {date.fromisoformat(key): value for key, value in spec['exceptions'].items()}
    if set(holidays) & set(exceptions) or any(not start <= day <= end for day in holidays | exceptions):
        raise ValueError('calendar holiday and exception dates must be distinct and in coverage')
    by_day = {}
    for regime in spec['regimes']:
        first, last = date.fromisoformat(regime['start']), date.fromisoformat(regime['end'])
        if not start <= first <= last <= end:
            raise ValueError('calendar regime outside coverage')
        for offset in range((last - first).days + 1):
            day = first + timedelta(days=offset)
            if day in by_day:
                raise ValueError(f'overlapping calendar regimes on {day}')
            by_day[day] = regime['hours']
    sessions, excluded = {}, {}
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        if day not in by_day:
            raise ValueError(f'calendar regime missing on {day}')
        override = exceptions.get(day)
        if override:
            if override['kind'] == 'regular':
                sessions[day] = _session(day, override['hours'])
            elif override['kind'] == 'excluded':
                excluded[day] = override['note']
            else:
                raise ValueError(f'unknown calendar exception kind on {day}')
        elif day in holidays:
            excluded[day] = holidays[day]
        elif day.weekday() < 5:
            sessions[day] = _session(day, by_day[day])
    return Calendar(start, end, sessions, excluded, spec['id'])
