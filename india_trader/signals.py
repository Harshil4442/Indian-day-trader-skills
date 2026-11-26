"""Point-in-time NSE cash equity setup research on regular-session candles."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from math import isfinite
from statistics import median

from .models import IST


SETUPS = ("orb", "vwap-reclaim", "trend-pullback")


@dataclass(frozen=True)
class SignalRules:
    interval_min: int = 5
    opening_range_min: int = 15
    entry_delay_min: int = 20
    latest_entry: str = "14:45"
    last_entry_buffer_min: int = 30
    lookback_sessions: int = 20
    min_baseline_sessions: int = 5
    min_price_inr: float = 50.0
    min_median_daily_turnover_inr: float = 100_000_000.0
    min_cumulative_rvol: float = 1.15
    min_bar_rvol: float = 1.35
    min_opening_range_bps: float = 15.0
    max_opening_range_bps: float = 250.0
    max_abs_gap_bps: float = 350.0
    breakout_buffer_bps: float = 0.0
    max_trigger_extension_bps: float = 80.0
    require_benchmark: bool = False
    min_benchmark_return_bps: float = -40.0
    min_relative_strength_bps: float = -20.0

    def __post_init__(self):
        for name in (
            "interval_min", "opening_range_min", "entry_delay_min", "last_entry_buffer_min",
            "lookback_sessions", "min_baseline_sessions",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.opening_range_min % self.interval_min:
            raise ValueError("opening_range_min must be a multiple of interval_min")
        if self.entry_delay_min < self.opening_range_min:
            raise ValueError("entry_delay_min cannot be earlier than the opening-range end")
        if self.min_baseline_sessions > self.lookback_sessions:
            raise ValueError("min_baseline_sessions cannot exceed lookback_sessions")
        for name in (
            "min_price_inr", "min_median_daily_turnover_inr", "min_cumulative_rvol",
            "min_bar_rvol", "min_opening_range_bps", "max_opening_range_bps", "max_abs_gap_bps",
            "breakout_buffer_bps", "max_trigger_extension_bps",
        ):
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if not (isfinite(self.min_benchmark_return_bps) and isfinite(self.min_relative_strength_bps)):
            raise ValueError("benchmark and relative-strength thresholds must be finite")
        if self.min_opening_range_bps >= self.max_opening_range_bps:
            raise ValueError("opening-range maximum must exceed opening-range minimum")
        if type(self.require_benchmark) is not bool:
            raise ValueError("require_benchmark must be a boolean")
        _local_time(self.latest_entry)


def _local_time(value):
    if not isinstance(value, str):
        raise ValueError("time of day must use HH:MM")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as error:
        raise ValueError("time of day must use HH:MM") from error
    if len(value) != 5 or parsed.tzinfo is not None:
        raise ValueError("time of day must use HH:MM")
    return parsed


DEFAULT_RULES = SignalRules()


def _ist(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware timestamp is required")
    return value.astimezone(IST)


def _deadline(session, rules):
    return min(
        datetime.combine(session.day, _local_time(rules.latest_entry), IST),
        session.closes - timedelta(minutes=rules.last_entry_buffer_min),
    )


def _index(bars, sessions, step, symbol=None):
    """Index valid regular-session slots and reject ambiguous duplicate candles."""
    series = {}
    seen_symbol = symbol
    for bar in bars:
        if seen_symbol is None:
            seen_symbol = bar.symbol
        if bar.symbol != seen_symbol:
            raise ValueError("Expected candle input for a single symbol")
        stamp = _ist(bar.ts)
        session = sessions.get(stamp.date())
        if session is None or session.kind != "regular":
            continue
        elapsed = stamp - session.opens
        if elapsed < timedelta() or stamp + step > session.closes:
            continue
        if elapsed % step:
            raise ValueError(f"{bar.symbol} candle is off the session grid at {stamp.isoformat()}")
        prices = (bar.open, bar.high, bar.low, bar.close)
        if not all(isfinite(value) and value > 0 for value in prices):
            raise ValueError(f"{bar.symbol} candle contains an invalid price at {stamp.isoformat()}")
        if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            raise ValueError(f"{bar.symbol} candle contains inconsistent OHLC at {stamp.isoformat()}")
        if type(bar.volume) is not int or bar.volume < 0:
            raise ValueError(f"{bar.symbol} candle contains invalid volume at {stamp.isoformat()}")
        day_bars = series.setdefault(session.day, {})
        slot = elapsed // step
        if slot in day_bars:
            raise ValueError(f"{bar.symbol} has duplicate candles at {stamp.isoformat()}")
        day_bars[slot] = bar
    return seen_symbol, series


def _series_until(day_bars, count):
    """Return a contiguous prefix of the requested length or None."""
    if any(slot not in day_bars for slot in range(count)):
        return None
    return [day_bars[slot] for slot in range(count)]


def _full_days(series, sessions, step, asof):
    return {
        day: _series_until(series.get(day, {}), (session.closes - session.opens) // step)
        for day, session in sorted(sessions.items())
        if session.kind == "regular" and session.closes <= asof
        and session.closes - session.opens >= step
    }


def _previous_days(full_days, day, rules):
    dates = [prior for prior in full_days if prior < day]
    recent = [full_days[prior] for prior in dates[-rules.lookback_sessions:] if full_days[prior]]
    prior_bars = full_days[dates[-1]] if dates else None
    return recent, prior_bars


def _typical(bar):
    return (bar.high + bar.low + bar.close) / 3


def _turnover(bars):
    return sum(_typical(bar) * bar.volume for bar in bars)


def _bps(value, level):
    return (value / level - 1) * 10_000


def _relative_volume(bars, baseline):
    samples = [session for session in baseline if len(session) >= len(bars)]
    if not samples:
        return None, None, 0
    index = len(bars) - 1
    cum_base = median(sum(bar.volume for bar in day[:len(bars)]) for day in samples)
    bar_base = median(day[index].volume for day in samples)
    cumulative = sum(bar.volume for bar in bars) / cum_base if cum_base else None
    at_slot = bars[-1].volume / bar_base if bar_base else None
    return cumulative, at_slot, len(samples)


def _vwap(bars):
    volume = 0
    turnover = 0
    values = []
    for bar in bars:
        volume += bar.volume
        turnover += _typical(bar) * bar.volume
        values.append(turnover / volume if volume else None)
    return values


def _structures(bars, vwap, range_high, range_low, rules):
    bar = bars[-1]
    prior = bars[-2]
    opening_break = range_high * (1 + rules.breakout_buffer_bps / 10_000)
    orb = prior.close <= opening_break < bar.close
    have_vwap = all(value is not None for value in vwap[-2:])
    reclaim = have_vwap and prior.close <= vwap[-2] and bar.close > max(vwap[-1], prior.high)
    trend = False
    pullback = bars[-3:-1]
    if len(bars) >= 6 and all(value is not None for value in vwap[-4:]):
        impulse = bars[-4]
        trend = (
            impulse.close > range_high and vwap[-1] > vwap[-4]
            and all(candle.close > level for candle, level in zip(pullback, vwap[-3:-1]))
            and min(candle.close for candle in pullback) < impulse.close
            and prior.close <= max(candle.high for candle in pullback) < bar.close
        )
    return {
        "orb": (orb, opening_break, (range_high + range_low) / 2, "close_above_opening_range"),
        "vwap-reclaim": (
            reclaim, max(vwap[-1] or bar.close, prior.high), min(prior.low, bar.low),
            "close_reclaims_vwap_and_prior_high",
        ),
        "trend-pullback": (
            trend, max(candle.high for candle in pullback),
            min(candle.low for candle in pullback), "close_breaks_vwap_supported_pullback",
        ),
    }


def _evaluate(symbol, bars, baseline, prior_bars, benchmark, rules, step):
    """Evaluate only a closed prefix of the current session."""
    bar = bars[-1]
    signal_at = _ist(bar.ts) + step
    opening_count = rules.opening_range_min // rules.interval_min
    range_high = max(item.high for item in bars[:opening_count])
    range_low = min(item.low for item in bars[:opening_count])
    width = (range_high - range_low) / bars[0].open * 10_000
    prev_close = prior_bars[-1].close if prior_bars else None
    gap = _bps(bars[0].open, prev_close) if prev_close else None
    baseline_turnover = median(map(_turnover, baseline)) if baseline else None
    vwap = _vwap(bars)
    cum_rvol, bar_rvol, sample = _relative_volume(bars, baseline)
    benchmark_return = _bps(benchmark[-1].close, benchmark[0].open) if benchmark else None
    stock_return = _bps(bar.close, bars[0].open)
    relative_strength = stock_return - benchmark_return if benchmark_return is not None else None
    blockers = []

    checks = (
        (prev_close is None, "previous_modeled_close_unavailable"),
        (gap is not None and abs(gap) > rules.max_abs_gap_bps, "opening_gap_too_large"),
        (not rules.min_opening_range_bps <= width <= rules.max_opening_range_bps,
         "opening_range_outside_bounds"),
        (bar.close < rules.min_price_inr, "price_below_minimum"),
        (baseline_turnover is None or baseline_turnover < rules.min_median_daily_turnover_inr,
         "median_turnover_below_minimum_or_unknown"),
        (sample < rules.min_baseline_sessions, "insufficient_volume_baseline"),
        (cum_rvol is None or cum_rvol < rules.min_cumulative_rvol, "cumulative_rvol_below_minimum"),
        (bar_rvol is None or bar_rvol < rules.min_bar_rvol, "bar_rvol_below_minimum"),
        (bar.volume == 0, "signal_candle_has_no_trades"),
        (vwap[-1] is None or bar.close <= vwap[-1], "close_not_above_vwap"),
        (rules.require_benchmark and benchmark_return is None, "benchmark_unavailable"),
        (benchmark_return is not None and benchmark_return < rules.min_benchmark_return_bps,
         "benchmark_below_minimum"),
        (relative_strength is not None and relative_strength < rules.min_relative_strength_bps,
         "relative_strength_below_minimum"),
    )
    blockers.extend(reason for failed, reason in checks if failed)
    metrics = {
        "last_close": bar.close, "bar_closed_at": signal_at.isoformat(),
        "opening_range_high": range_high, "opening_range_low": range_low,
        "opening_range_width_bps": width, "previous_modeled_close": prev_close,
        "opening_gap_bps": gap, "vwap": vwap[-1], "cumulative_rvol": cum_rvol,
        "bar_rvol": bar_rvol, "baseline_sessions": sample,
        "median_modeled_daily_turnover_inr": baseline_turnover,
        "modeled_turnover_to_now_inr": _turnover(bars),
        "session_return_bps": stock_return, "benchmark_return_bps": benchmark_return,
        "relative_strength_bps": relative_strength,
    }
    setups = {}
    for setup, (trigger, level, stop, reason) in _structures(
        bars, vwap, range_high, range_low, rules
    ).items():
        status = "triggered" if trigger else "waiting"
        reasons = [] if trigger else ["confirmation_pattern_absent"]
        if trigger:
            reasons = list(blockers)
            if _bps(bar.close, level) > rules.max_trigger_extension_bps:
                reasons.append("close_too_far_above_trigger")
            if not 0 < stop < bar.close:
                reasons.append("invalid_structural_stop")
            if reasons:
                status = "blocked"
        setups[setup] = {
            "symbol": symbol, "setup": setup, "status": status, "reasons": reasons,
            "signal_time": signal_at.isoformat() if trigger else None,
            "entry": bar.close if trigger else None, "stop": stop,
            "trigger_level": level, "reason": reason,
        }
    return blockers, metrics, setups


def _signal_snapshots(symbol, bars, baseline, prior_bars, benchmark, session, rules, step):
    """Yield closed prefixes in the configured entry window."""
    first_count = rules.opening_range_min // rules.interval_min + 1
    earliest = session.opens + timedelta(minutes=rules.entry_delay_min)
    latest = _deadline(session, rules)
    for count in range(first_count, len(bars) + 1):
        close_at = _ist(bars[count - 1].ts) + step
        if close_at < earliest:
            continue
        if close_at > latest:
            break
        benchmark_prefix = _series_until(benchmark, count)
        yield _evaluate(
            symbol, bars[:count], baseline, prior_bars, benchmark_prefix, rules, step,
        )


def analyze(bars, sessions, asof, *, benchmark=(), rules=DEFAULT_RULES, symbol=None):
    """Check a symbol's opening-range, VWAP and trend pullback setups as of IST time.

    Only the first qualifying confirmation per setup per session is eligible.
    A past confirmation is visible as already_triggered, never as a fresh signal.
    """
    asof = _ist(asof)
    step = timedelta(minutes=rules.interval_min)
    symbol, series = _index(bars, sessions, step, symbol)
    benchmark_symbol, market = _index(benchmark, sessions, step)
    session = sessions.get(asof.date())
    result = {
        "symbol": symbol, "asof": asof.isoformat(), "status": "waiting", "reasons": [],
        "benchmark_symbol": benchmark_symbol, "metrics": {}, "setups": {}, "signals": [],
    }
    if session is None or session.kind != "regular":
        result.update(status="closed", reasons=["no_regular_session_in_calendar"])
        return result
    result["entry_deadline"] = _deadline(session, rules).isoformat()
    if asof < session.opens:
        result.update(status="waiting", reasons=["regular_session_has_not_opened"])
        return result
    if asof > _deadline(session, rules):
        result.update(status="expired", reasons=["entry_window_closed"])
        return result
    count = (asof - session.opens) // step
    if count <= rules.opening_range_min // rules.interval_min or (
        session.opens + step * count < session.opens + timedelta(minutes=rules.entry_delay_min)
    ):
        result.update(status="waiting", reasons=["first_confirmation_candle_not_closed"])
        return result
    current = _series_until(series.get(session.day, {}), count)
    if current is None:
        result.update(status="blocked", reasons=["missing_or_stale_closed_candles"])
        return result
    today_benchmark = market.get(session.day, {})
    if benchmark_symbol and _series_until(today_benchmark, count) is None:
        result.update(status="blocked", reasons=["missing_or_stale_benchmark_candles"])
        return result
    baseline, previous = _previous_days(_full_days(series, sessions, step, asof), session.day, rules)
    confirmed = {}
    for blockers, metrics, setups in _signal_snapshots(
        symbol, current, baseline, previous, today_benchmark, session, rules, step,
    ):
        for setup, details in setups.items():
            if details["status"] == "triggered":
                confirmed.setdefault(setup, details)
    for setup, first in confirmed.items():
        if first["signal_time"] != metrics["bar_closed_at"]:
            setups[setup] = dict(first, status="already_triggered", reasons=["earlier_confirmation"])
    signals = [item for item in setups.values() if item["status"] == "triggered"]
    status = "triggered" if signals else ("blocked" if blockers or any(
        item["status"] == "blocked" for item in setups.values()
    ) else "waiting")
    result.update(status=status, reasons=blockers, metrics=metrics, setups=setups, signals=signals)
    return result


def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    changes = [new - old for old, new in zip(closes, closes[1:])]
    up = sum(max(change, 0) for change in changes[:period]) / period
    down = sum(max(-change, 0) for change in changes[:period]) / period
    for change in changes[period:]:
        up = (up * (period - 1) + max(change, 0)) / period
        down = (down * (period - 1) + max(-change, 0)) / period
    if not down:
        return 100.0 if up else 50.0
    return 100 - 100 / (1 + up / down)


def premarket(bars, sessions, asof, *, rules=DEFAULT_RULES, symbol=None):
    """Summarize completed prior sessions from the regular-session candle grid."""
    asof = _ist(asof)
    step = timedelta(minutes=rules.interval_min)
    symbol, series = _index(bars, sessions, step, symbol)
    complete_days = _full_days(series, sessions, step, asof)
    if not complete_days:
        return {"symbol": symbol, "asof": asof.isoformat(), "status": "insufficient_history",
                "reasons": ["no_completed_calendar_session"], "setups": []}
    dates = list(complete_days)
    if complete_days[dates[-1]] is None:
        return {"symbol": symbol, "asof": asof.isoformat(), "status": "insufficient_history",
                "reasons": ["latest_completed_session_missing_candles"], "setups": []}
    continuous = []
    for day in reversed(dates):
        if complete_days[day] is None:
            break
        continuous.append(complete_days[day])
    days = list(reversed(continuous))[-(max(rules.lookback_sessions, 30) + 1):]
    highs = [max(bar.high for bar in day) for day in days]
    lows = [min(bar.low for bar in day) for day in days]
    closes = [day[-1].close for day in days]
    ranges = [high - low for high, low in zip(highs, lows)]
    breakout = len(days) >= 21 and closes[-1] > max(highs[-21:-1])
    inside = len(days) >= 2 and highs[-1] < highs[-2] and lows[-1] > lows[-2]
    narrow = len(days) >= 7 and ranges[-1] < min(ranges[-7:-1])
    average = sum(closes[-20:]) / 20 if len(days) >= 20 else None
    average_reclaim = (len(days) >= 21 and closes[-2] <= sum(closes[-21:-1]) / 20
                       and closes[-1] > average)
    rsi = _rsi(closes)
    prior_rsi = _rsi(closes[:-1])
    true_ranges = [
        max(high - low, abs(high - prev), abs(low - prev))
        for high, low, prev in zip(highs[1:], lows[1:], closes[:-1])
    ]
    turnover = median(map(_turnover, days[-rules.lookback_sessions:]))
    reasons = []
    if len(days) < rules.min_baseline_sessions:
        reasons.append("insufficient_complete_sessions")
    if closes[-1] < rules.min_price_inr:
        reasons.append("price_below_minimum")
    if turnover < rules.min_median_daily_turnover_inr:
        reasons.append("median_turnover_below_minimum")
    setups = [
        name for name, match in (
            ("close_above_20_session_high", breakout), ("inside_session", inside), ("nr7", narrow),
            ("reclaimed_average_close_20", average_reclaim), ("rsi14_oversold", rsi is not None and rsi < 30),
            ("rsi14_oversold_bounce", prior_rsi is not None and prior_rsi < 30 and closes[-1] > closes[-2]),
        ) if match
    ]
    return {
        "symbol": symbol, "asof": asof.isoformat(), "status": "filtered" if reasons else "ready",
        "reasons": reasons, "setups": setups, "complete_consecutive_sessions": len(days),
        "latest_modeled_session": dates[-1].isoformat(), "last_modeled_close": closes[-1],
        "last_modeled_session_high": highs[-1], "last_modeled_session_low": lows[-1],
        "high_20_prior_sessions": max(highs[-21:-1]) if len(days) >= 21 else None,
        "average_close_20": average,
        "atr14_simple": sum(true_ranges[-14:]) / 14 if len(true_ranges) >= 14 else None,
        "rsi14_wilder": rsi, "median_modeled_daily_turnover_inr": turnover,
    }
