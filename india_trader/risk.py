"""Cash-equity planning, estimated charges, and paper admission checks."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from math import isfinite

from .models import Bar, Instrument, IST, Session

BAR_INTERVAL = timedelta(minutes=5)


class RiskBlocked(ValueError):
    """A named, user-visible reason why a paper action cannot proceed."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


def local(when: datetime) -> datetime:
    if when.utcoffset() is None:
        raise ValueError("timestamp must include a time zone")
    return when.astimezone(IST)


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _tick(price: float, tick_size: float, rounding: str) -> float:
    if not all(isfinite(value) and value > 0 for value in (price, tick_size)):
        raise ValueError("price and tick must be positive and finite")
    tick = _decimal(tick_size)
    return float((_decimal(price) / tick).to_integral_value(rounding=rounding) * tick)


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_CEILING))


@dataclass(frozen=True)
class Costs:
    """One executed buy and one executed sell; fractional rates and INR caps.

    Verified 2026-11-26 against https://zerodha.com/charges and
    https://nsearchives.nseindia.com/content/circulars/FA73061.pdf.
    Uses sell turnover as the STT basis for a single modeled exit and keeps
    unrounded component levies until the aggregate. Brokerage contract notes
    can round or aggregate STT and other levies differently.
    """

    brokerage_rate: float = 0.0003
    brokerage_cap: float = 20.0
    exchange_rate: float = 0.0000307
    sebi_rate: float = 0.000001
    stamp_rate: float = 0.00003
    stt_rate: float = 0.00025
    gst_rate: float = 0.18
    slippage_bps: float = 5.0
    checked_on: str = "2026-11-26"

    def __post_init__(self) -> None:
        rates = (
            self.brokerage_rate, self.exchange_rate, self.sebi_rate,
            self.stamp_rate, self.stt_rate, self.gst_rate,
        )
        if any(not 0 <= rate < 1 for rate in rates):
            raise ValueError("cost rates must be fractions between zero and one")
        if not isfinite(self.brokerage_cap) or self.brokerage_cap < 0:
            raise ValueError("brokerage cap must be a finite, nonnegative amount")
        if not 0 <= self.slippage_bps < 10_000:
            raise ValueError("slippage_bps must be from zero to below 10000")
        date.fromisoformat(self.checked_on)

    def buy_price(self, price: float, tick_size: float) -> float:
        adjusted = _decimal(price) * (1 + _decimal(self.slippage_bps) / 10_000)
        return _tick(float(adjusted), tick_size, ROUND_CEILING)

    def sell_price(self, price: float, tick_size: float) -> float:
        adjusted = _decimal(price) * (1 - _decimal(self.slippage_bps) / 10_000)
        return max(tick_size, _tick(float(adjusted), tick_size, ROUND_FLOOR))

    def round_trip(self, entry: float, exit: float, quantity: int) -> float:
        if type(quantity) is not int or quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        if not all(isfinite(value) and value > 0 for value in (entry, exit)):
            raise ValueError("execution prices must be positive and finite")
        buy = _decimal(entry) * quantity
        sell = _decimal(exit) * quantity
        taxable = (buy + sell) * (_decimal(self.exchange_rate) + _decimal(self.sebi_rate))
        taxable += min(_decimal(self.brokerage_cap), buy * _decimal(self.brokerage_rate))
        taxable += min(_decimal(self.brokerage_cap), sell * _decimal(self.brokerage_rate))
        fees = taxable * (1 + _decimal(self.gst_rate))
        fees += buy * _decimal(self.stamp_rate) + sell * _decimal(self.stt_rate)
        return _money(fees)


@dataclass(frozen=True)
class RiskConfig:
    initial_cash: float = 100_000
    max_trade_risk: float = 500
    max_daily_loss: float = 1_500
    max_trades_per_day: int = 3
    max_open_positions: int = 2
    max_position_fraction: float = 0.25
    min_net_rr: float = 1.25
    entry_deadline: str = "14:45"
    square_off: str = "15:05"
    ticket_ttl_minutes: int = 5
    max_signal_age_minutes: int = 15
    max_entry_drift_bps: float = 20
    costs: Costs = field(default_factory=Costs)

    def __post_init__(self) -> None:
        amounts = (self.initial_cash, self.max_trade_risk, self.max_daily_loss, self.min_net_rr)
        if any(not isfinite(value) or value <= 0 for value in amounts):
            raise ValueError("cash, risk bounds and minimum net reward/risk must be positive and finite")
        counts = (
            self.max_trades_per_day, self.max_open_positions,
            self.ticket_ttl_minutes, self.max_signal_age_minutes,
        )
        if any(type(count) is not int or count <= 0 for count in counts):
            raise ValueError("risk counts and minute limits must be positive integers")
        if not 0 < self.max_position_fraction <= 1 or not 0 <= self.max_entry_drift_bps < 10_000:
            raise ValueError("invalid maximum allocation fraction or entry drift")
        deadlines = (time.fromisoformat(self.entry_deadline), time.fromisoformat(self.square_off))
        if any(t.tzinfo is not None or t.second or t.microsecond or t.minute % 5 for t in deadlines):
            raise ValueError("entry_deadline and square_off must be local five-minute boundaries")
        if deadlines[0] >= deadlines[1]:
            raise ValueError("entry_deadline must precede square_off")
        if not isinstance(self.costs, Costs):
            raise ValueError("costs must be a Costs instance")


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    setup: str
    signal_at: datetime
    entry: float
    stop: float
    target: float

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.setup.strip():
            raise ValueError("symbol and setup are required")
        local(self.signal_at)
        if not all(isfinite(value) for value in (self.stop, self.entry, self.target)):
            raise ValueError("plan prices must be finite")
        if not 0 < self.stop < self.entry < self.target:
            raise ValueError("a long plan requires 0 < stop < entry < target")


def make_plan(
    symbol: str, setup: str, signal_at: datetime, entry: float, stop: float, target_r: float = 2.0,
) -> TradePlan:
    target = _decimal(entry) + (_decimal(entry) - _decimal(stop)) * _decimal(target_r)
    return TradePlan(symbol, setup, local(signal_at), entry, stop, float(target))


@dataclass(frozen=True)
class AccountRisk:
    equity: float
    available_cash: float
    realized_today: float
    committed_risk: float
    trades_today: int
    open_symbols: tuple[str, ...]
    prior_session_exposure: bool = False


@dataclass(frozen=True)
class Ticket:
    plan: TradePlan
    as_of: datetime
    expires_at: datetime
    session_close: datetime
    latest_entry_at: datetime
    flatten_at: datetime
    tick_size: float
    lower_band: float
    upper_band: float
    instrument_asof: datetime
    instrument_source: str
    instrument_isin: str
    observed_bar_at: datetime
    observed_close: float
    quantity: int
    expected_entry: float
    maximum_entry: float
    stop: float
    target: float
    estimated_stop_fill: float
    estimated_target_fill: float
    maximum_cash: float
    planned_loss: float
    planned_gain: float
    net_reward_risk: float
    estimated_stop_fees: float
    estimated_target_fees: float
    digest: str = ""


def deadlines(session: Session, config: RiskConfig) -> tuple[datetime, datetime]:
    """Keep at least one bar between the modeled flatten and continuous close."""
    flatten = min(
        datetime.combine(session.day, time.fromisoformat(config.square_off), IST),
        local(session.closes) - BAR_INTERVAL,
    )
    last_entry = min(
        datetime.combine(session.day, time.fromisoformat(config.entry_deadline), IST),
        flatten - BAR_INTERVAL,
    )
    return last_entry, flatten


def entry_bar_start(now: datetime, session: Session) -> datetime:
    now = local(now)
    start = local(session.opens)
    intervals, remainder = divmod(now - start, BAR_INTERVAL)
    return start + (intervals + bool(remainder)) * BAR_INTERVAL


def _timing(
    plan: TradePlan, session: Session, latest_bar: Bar, now: datetime, config: RiskConfig,
) -> tuple[datetime, datetime]:
    now = local(now)
    signal_at = local(plan.signal_at)
    bar_start = local(latest_bar.ts)
    bar_end = bar_start + BAR_INTERVAL
    last_entry, flatten = deadlines(session, config)
    if session.kind != "regular" or session.day != now.date() or not session.opens <= now:
        raise RiskBlocked("OUTSIDE_REGULAR_SESSION")
    if now >= last_entry or entry_bar_start(now, session) >= last_entry:
        raise RiskBlocked("ENTRY_CUTOFF")
    if not session.opens < signal_at <= now or now - signal_at >= timedelta(minutes=config.max_signal_age_minutes):
        raise RiskBlocked("SIGNAL_STALE_OR_FUTURE")
    if (
        latest_bar.symbol != plan.symbol or bar_start < session.opens
        or (bar_start - session.opens) % BAR_INTERVAL
        or not signal_at <= bar_end <= now < bar_end + BAR_INTERVAL
        or latest_bar.volume <= 0 or not isfinite(latest_bar.close) or latest_bar.close <= 0
    ):
        raise RiskBlocked("STALE_OR_INVALID_CANDLE")
    return last_entry, flatten


def _market_metadata(
    instrument: Instrument, plan: TradePlan, now: datetime,
) -> tuple[float, float, float]:
    if instrument.symbol != plan.symbol or instrument.exchange != "NSE" or instrument.series != "EQ":
        raise RiskBlocked("INELIGIBLE_INSTRUMENT")
    if instrument.status != "clear":
        raise RiskBlocked("INSTRUMENT_RESTRICTED_OR_UNVERIFIED", instrument.status)
    if instrument.asof is None or not instrument.source.strip() or not instrument.isin.strip():
        raise RiskBlocked("MISSING_INSTRUMENT_EVIDENCE")
    snapshot = local(instrument.asof)
    if snapshot.date() != now.date() or snapshot > now:
        raise RiskBlocked("INSTRUMENT_EVIDENCE_STALE_OR_FUTURE")
    tick, lower, upper = instrument.tick_size, instrument.lower_band, instrument.upper_band
    if any(value is None or not isfinite(value) or value <= 0 for value in (tick, lower, upper)):
        raise RiskBlocked("MISSING_TICK_OR_PRICE_BAND")
    if tick >= lower or lower >= upper:
        raise RiskBlocked("INVALID_TICK_OR_PRICE_BAND")
    return tick, lower, upper


def size_ticket(
    plan: TradePlan, instrument: Instrument, session: Session, latest_bar: Bar,
    now: datetime, config: RiskConfig, account: AccountRisk,
) -> Ticket:
    """Recheck from source inputs and size against stop loss, cash and day risk."""
    now = local(now)
    last_entry, flatten = _timing(plan, session, latest_bar, now, config)
    tick, lower, upper = _market_metadata(instrument, plan, now)
    if account.prior_session_exposure:
        raise RiskBlocked("UNRESOLVED_PRIOR_SESSION")
    if plan.symbol in account.open_symbols:
        raise RiskBlocked("SYMBOL_ALREADY_PENDING_OR_OPEN")
    if len(account.open_symbols) >= config.max_open_positions:
        raise RiskBlocked("OPEN_POSITION_LIMIT")
    if account.trades_today >= config.max_trades_per_day:
        raise RiskBlocked("DAILY_TRADE_LIMIT")

    remaining_loss = config.max_daily_loss - max(0.0, -account.realized_today) - account.committed_risk
    if remaining_loss <= 0:
        raise RiskBlocked("DAILY_LOSS_LIMIT")
    cash_limit = min(account.available_cash, account.equity * config.max_position_fraction)
    maximum_drift = _decimal(plan.entry) * _decimal(config.max_entry_drift_bps) / 10_000
    if abs(_decimal(latest_bar.close) - _decimal(plan.entry)) > maximum_drift:
        raise RiskBlocked("ENTRY_DRIFT_EXCEEDED")
    stop = _tick(plan.stop, tick, ROUND_FLOOR)
    target = _tick(plan.target, tick, ROUND_CEILING)
    expected_entry = config.costs.buy_price(plan.entry, tick)
    maximum_entry = config.costs.buy_price(float(_decimal(plan.entry) + maximum_drift), tick)
    stop_fill = config.costs.sell_price(stop, tick) if stop > tick else stop
    target_fill = config.costs.sell_price(target, tick)
    if not lower < stop_fill <= stop < expected_entry <= maximum_entry < target_fill <= target < upper:
        raise RiskBlocked("PLAN_OUTSIDE_BANDS_OR_COLLAPSES_AFTER_COSTS")
    if not lower <= latest_bar.low <= latest_bar.high <= upper:
        raise RiskBlocked("CANDLE_OUTSIDE_INSTRUMENT_BANDS")
    if not stop < latest_bar.close < target or latest_bar.low == latest_bar.high in (lower, upper):
        raise RiskBlocked("CANDLE_AT_BAND_OR_EXIT_LEVEL")

    risk_limit = min(config.max_trade_risk, remaining_loss)
    price_risk = _decimal(maximum_entry) - _decimal(stop_fill)
    cash_available = _decimal(cash_limit)
    risk_available = _decimal(risk_limit)
    upper_quantity = max(0, min(int(cash_available // _decimal(maximum_entry)), int(risk_available // price_risk)))

    def values(quantity: int) -> tuple[Decimal, Decimal, float]:
        fee = config.costs.round_trip(maximum_entry, stop_fill, quantity)
        return _decimal(maximum_entry) * quantity + _decimal(fee), price_risk * quantity + _decimal(fee), fee

    lower_quantity = 0
    while lower_quantity < upper_quantity:
        midpoint = (lower_quantity + upper_quantity + 1) // 2
        cash, loss, _ = values(midpoint)
        if cash <= cash_available and loss <= risk_available:
            lower_quantity = midpoint
        else:
            upper_quantity = midpoint - 1
    quantity = lower_quantity
    if quantity < 1:
        raise RiskBlocked("CASH_OR_RISK_BUDGET_TOO_SMALL")

    maximum_cash, loss, stop_fees = values(quantity)
    target_fees = config.costs.round_trip(maximum_entry, target_fill, quantity)
    gain = (_decimal(target_fill) - _decimal(maximum_entry)) * quantity - _decimal(target_fees)
    net_rr = float(gain / loss)
    if net_rr < config.min_net_rr:
        raise RiskBlocked("NET_REWARD_RISK_TOO_LOW", f"{net_rr:.2f} < {config.min_net_rr:.2f}")

    expires = min(
        now + timedelta(minutes=config.ticket_ttl_minutes),
        local(plan.signal_at) + timedelta(minutes=config.max_signal_age_minutes),
        last_entry,
    )
    return Ticket(
        plan, now, expires, local(session.closes), last_entry, flatten, tick, lower, upper,
        local(instrument.asof), instrument.source, instrument.isin, local(latest_bar.ts),
        latest_bar.close, quantity, expected_entry, maximum_entry, stop, target, stop_fill,
        target_fill, _money(maximum_cash), _money(loss), float(gain), net_rr, stop_fees, target_fees,
    )
