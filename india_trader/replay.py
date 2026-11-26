"""Historical rule replay and an optional matched-session bootstrap."""

from collections import Counter
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from math import isfinite
from random import Random
from statistics import mean, median

from .models import Bar, IST
from .signals import (
    DEFAULT_RULES, SETUPS, _deadline, _full_days, _index, _ist, _local_time,
    _previous_days, _series_until, _signal_snapshots,
)


def _flat_time(session, step, flat_by, flat_buffer_min):
    limit = min(
        datetime.combine(session.day, _local_time(flat_by), IST),
        session.closes - timedelta(minutes=flat_buffer_min),
    )
    slots = max(0, (limit - session.opens) // step)
    return session.opens + step * slots, slots


def _execution_levels(entry, stop, target, tick_size):
    tick = Decimal(str(tick_size))
    floor_stop = (Decimal(str(stop)) / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    ceil_target = (Decimal(str(target)) / tick).to_integral_value(rounding=ROUND_CEILING) * tick
    return float(entry), float(floor_stop), float(ceil_target)


def _levels(signal, target_r, tick_size):
    reference = Decimal(str(signal["entry"]))
    stop = Decimal(str(signal["stop"]))
    target = reference + Decimal(str(target_r)) * (reference - stop)
    return _execution_levels(reference, stop, target, tick_size)


def _fill_path(path, entry, stop, target, *, closed_at_flat, costs, tick_size, quantity,
               max_entry_drift_bps):
    """Evaluate open, price barriers and flat exit with adverse fills."""
    if not 0 < stop < entry < target:
        return {"outcome": "invalid_levels"}
    if not path:
        return {"outcome": "missing_next_open"}
    opening = path[0]
    if not opening.volume:
        return {"outcome": "no_trade_at_next_open"}
    paid = costs.buy_price(opening.open, tick_size)
    reference = Decimal(str(entry))
    max_drift = reference * Decimal(str(max_entry_drift_bps)) / 10_000
    maximum_entry = costs.buy_price(float(reference + max_drift), tick_size)
    if not stop < opening.open < target or not stop < paid < target or paid > maximum_entry:
        return {"outcome": "next_open_outside_entry_limits"}

    last = None
    outcome = None
    for bar in path:
        if not bar.volume:
            return {"outcome": "unresolved_after_entry", "entry_fill": paid}
        last = bar
        if bar.open <= stop:
            outcome, price = "stop", bar.open
        elif bar.open >= target:
            outcome, price = "target", target
        elif bar.low <= stop:
            # OHLC has no event ordering when both barriers occur inside the candle.
            outcome, price = "stop", stop
        elif bar.high >= target:
            outcome, price = "target", target
        if outcome:
            break
    if not outcome:
        if not closed_at_flat:
            return {"outcome": "unresolved_after_entry", "entry_fill": paid}
        outcome, price = "flat", last.close

    received = costs.sell_price(price, tick_size)
    fees = costs.round_trip(paid, received, quantity)
    net = (received - paid) * quantity - fees
    return {
        "outcome": outcome, "entry_fill": paid, "exit_fill": received,
        "exit_candle_start": _ist(last.ts).isoformat(),
        "fees_inr": round(fees, 4), "net_inr": round(net, 4),
        "net_r": round(net / ((entry - stop) * quantity), 6),
    }


def _summary(observations):
    counts = Counter(item["outcome"] for item in observations)
    completed = [item for item in observations if "net_r" in item]
    count = len(completed)
    return {
        "observations": len(observations), "resolved_fills": count,
        "unresolved_fills": counts["unresolved_after_entry"],
        "outcome_counts": dict(sorted(counts.items())),
        "completed_fractions": {
            name: counts[name] / count for name in ("target", "stop", "flat")
        } if count else {},
        "mean_net_r": mean(item["net_r"] for item in completed) if count else None,
        "median_net_r": median(item["net_r"] for item in completed) if count else None,
        "positive_net_fraction": sum(item["net_r"] > 0 for item in completed) / count if count else None,
    }


def _histories(symbol, series, market, sessions, full, asof, *, setup, rules, step, history_limit,
               entry_before, flat_by, flat_buffer_min, target_r, fill_kwargs):
    dates = [day for day, session in sorted(sessions.items())
             if day < asof.date() and session.kind == "regular"][-history_limit:]
    observations = []
    for day in dates:
        session = sessions[day]
        raw = series.get(day, {})
        _, flat_count = _flat_time(session, step, flat_by, flat_buffer_min)
        prefix = []
        for slot in range(flat_count):
            if slot not in raw:
                break
            prefix.append(raw[slot])
        baseline, previous = _previous_days(full, day, rules)
        result = {"date": day.isoformat()}
        if not prefix:
            outcome = "incomplete_candles_before_signal" if raw else "no_session_candles"
            observations.append(dict(result, outcome=outcome))
            continue
        if previous is None or len(baseline) < rules.min_baseline_sessions:
            observations.append(dict(result, outcome="insufficient_prior_baseline"))
            continue
        risk_cutoff = datetime.combine(day, _local_time(entry_before), IST)
        deadline = min(_deadline(session, rules), risk_cutoff - timedelta(microseconds=1),
                       session.opens + step * (flat_count - 1))
        entry_count = max(0, (deadline - session.opens) // step)
        benchmark_raw = market.get(day, {})
        benchmark_cover = 0
        while benchmark_cover in benchmark_raw and benchmark_cover < entry_count:
            benchmark_cover += 1
        search_count = min(entry_count, benchmark_cover) if rules.require_benchmark else entry_count
        signal = None
        for _, _, snapshots in _signal_snapshots(
            symbol, prefix[:search_count], baseline, previous, benchmark_raw, session, rules, step,
        ):
            if snapshots[setup]["status"] == "triggered":
                signal = snapshots[setup]
                break
        if signal is None:
            outcome = "no_signal"
            if benchmark_cover < entry_count and rules.require_benchmark:
                outcome = "incomplete_benchmark_before_signal"
            elif len(prefix) < entry_count:
                outcome = "incomplete_candles_before_signal"
            observations.append(dict(result, outcome=outcome))
            continue
        signal_at = datetime.fromisoformat(signal["signal_time"])
        slot = (signal_at - session.opens) // step
        entry, stop, target = _levels(signal, target_r, fill_kwargs["tick_size"])
        outcome = _fill_path(
            prefix[slot:], entry, stop, target, closed_at_flat=len(prefix) == flat_count, **fill_kwargs,
        )
        observations.append(dict(
            result, signal_time=signal["signal_time"], entry_reference=entry, stop=stop, target=target,
            **outcome,
        ))
    return dict(_summary(observations), sessions=observations)


def _plan_data(plan, symbol, setup, asof, current, sessions, step):
    if is_dataclass(plan):
        plan = asdict(plan)
    if plan.get("symbol", symbol) != symbol or plan.get("setup", setup) != setup:
        raise ValueError("Plan must match replay symbol and setup")
    time_value = plan.get("signal_time", plan.get("signal_at"))
    if isinstance(time_value, str):
        time_value = datetime.fromisoformat(time_value)
    if not isinstance(time_value, datetime):
        raise ValueError("Plan needs a signal_time or signal_at timestamp")
    signal_at = _ist(time_value)
    if signal_at.date() != asof.date() or signal_at > asof:
        raise ValueError("Plan signal must be on the as-of session and already closed")
    session = sessions.get(signal_at.date())
    if not session or session.kind != "regular":
        raise ValueError("Plan does not belong to a regular session")
    elapsed = signal_at - session.opens
    if elapsed < step or elapsed % step or signal_at > session.closes:
        raise ValueError("Plan signal does not align to a completed session candle")
    slot = elapsed // step
    observed = _series_until(current.get(signal_at.date(), {}), slot)
    if observed is None or not observed[-1].volume:
        raise ValueError("Plan signal needs contiguous observed candles with trades at confirmation")
    try:
        entry, stop, target = (float(plan[field]) for field in ("entry", "stop", "target"))
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError("Plan needs numeric entry, stop and target fields") from error
    if not (all(isfinite(value) for value in (entry, stop, target)) and 0 < stop < entry < target):
        raise ValueError("Plan needs finite prices with stop below entry below target")
    return session, slot, signal_at, entry, stop, target


def _bootstrap(plan, symbol, setup, asof, series, sessions, full, step, *,
               entry_before, flat_by, flat_buffer_min, simulations, seed, min_sim_sessions, history_limit,
               fill_kwargs):
    session, slot, signal_at, entry, stop, target = _plan_data(
        plan, symbol, setup, asof, series, sessions, step,
    )
    entry, stop, target = _execution_levels(entry, stop, target, fill_kwargs["tick_size"])
    if not 0 < stop < entry < target:
        raise ValueError("Plan levels collapse when rounded to the supplied tick")
    flat_at, flat_count = _flat_time(session, step, flat_by, flat_buffer_min)
    if signal_at >= datetime.combine(session.day, _local_time(entry_before), IST):
        raise ValueError("Plan signal is at or after the modeled entry cutoff")
    if flat_count <= slot:
        raise ValueError("Plan leaves no candle before the flat deadline")
    dates = [day for day in full if day < session.day][-history_limit:]
    samples = [
        bars for day in dates if (bars := full[day]) is not None
        and len(bars) >= flat_count and all(bar.volume for bar in bars[slot - 1:flat_count])
    ]
    report = {
        "status": "insufficient_matched_sessions", "requested_draws": simulations,
        "matched_sessions": len(samples), "minimum_matched_sessions": min_sim_sessions,
        "seed": seed, "signal_time": signal_at.isoformat(), "flat_at": flat_at.isoformat(),
        "source": "complete prior session paths at the same elapsed times, scaled to plan entry",
        "counts_note": "Draws reuse source sessions and are dependent on the observed sample.",
    }
    if len(samples) < min_sim_sessions:
        return report
    rng = Random(seed)
    observations = []
    for _ in range(simulations):
        sampled = rng.choice(samples)
        scale = entry / sampled[slot - 1].close
        path = [
            Bar(symbol, session.opens + step * (slot + offset),
                bar.open * scale, bar.high * scale, bar.low * scale, bar.close * scale, bar.volume)
            for offset, bar in enumerate(sampled[slot:flat_count])
        ]
        observations.append(_fill_path(
            path, entry, stop, target, closed_at_flat=True, **fill_kwargs,
        ))
    return dict(report, **_summary(observations), status="simulated")


def replay(bars, sessions, asof, *, benchmark=(), rules=DEFAULT_RULES, symbol=None,
           setup="orb", costs=None, tick_size=0.05, quantity=1, target_r=2.0,
           flat_by="15:05", flat_buffer_min=10, simulations=1_000, seed=19, plan=None,
           min_sim_sessions=20, max_entry_drift_bps=20.0, history_limit=60, entry_before="14:45"):
    """Replay first confirmed signals on prior dated sessions, plus optional plan scenarios."""
    if setup not in SETUPS:
        raise ValueError(f"Unknown setup {setup!r}; choose from {', '.join(SETUPS)}")
    for name, value in (
        ("quantity", quantity), ("flat_buffer_min", flat_buffer_min), ("history_limit", history_limit),
        ("min_sim_sessions", min_sim_sessions), ("simulations", simulations),
    ):
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if simulations > 100_000:
        raise ValueError("simulations cannot exceed 100000")
    for name, value in (("tick_size", tick_size), ("target_r", target_r)):
        if not isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    if not 0 <= max_entry_drift_bps < 10_000:
        raise ValueError("max_entry_drift_bps must be from zero to below 10000")
    _local_time(entry_before)
    _local_time(flat_by)
    if costs is None:
        from .risk import Costs

        costs = Costs()
    asof = _ist(asof)
    step = timedelta(minutes=rules.interval_min)
    symbol, series = _index(bars, sessions, step, symbol)
    if symbol is None:
        raise ValueError("Replay requires candles or a symbol")
    benchmark_symbol, market = _index(benchmark, sessions, step)
    if benchmark_symbol:
        rules = replace(rules, require_benchmark=True)
    full = _full_days(series, sessions, step, asof)
    fill_kwargs = {
        "costs": costs, "tick_size": tick_size, "quantity": quantity,
        "max_entry_drift_bps": max_entry_drift_bps,
    }
    history = _histories(
        symbol, series, market, sessions, full, asof, setup=setup, rules=rules, step=step,
        history_limit=history_limit, entry_before=entry_before, flat_by=flat_by, flat_buffer_min=flat_buffer_min,
        target_r=target_r, fill_kwargs=fill_kwargs,
    )
    bootstrap = None
    if plan is not None:
        bootstrap = _bootstrap(
            plan, symbol, setup, asof, series, sessions, full, step, entry_before=entry_before, flat_by=flat_by,
            flat_buffer_min=flat_buffer_min, simulations=simulations, seed=seed,
            min_sim_sessions=min_sim_sessions, history_limit=history_limit, fill_kwargs=fill_kwargs,
        )
    return {
        "symbol": symbol, "setup": setup, "asof": asof.isoformat(),
        "benchmark_symbol": benchmark_symbol, "quantity": quantity, "tick_size": tick_size,
        "target_r": target_r, "entry_before_ist": entry_before, "flat_by_ist": flat_by,
        "flat_buffer_min": flat_buffer_min,
        "max_entry_drift_bps": max_entry_drift_bps, "history_limit": history_limit,
        "history": history, "bootstrap": bootstrap,
        "conventions": [
            "One first qualifying signal per prior session; baseline uses earlier modeled sessions.",
            "Entry reference is the signal close; the next-open fill must fit the slipped and tick-rounded entry cap.",
            "Structural stop and target stay fixed; stop gaps use the adverse open.",
            "An ambiguous stop/target candle resolves to the stop; the timed flat uses a candle close.",
            "Coverage gaps can leave trades unresolved; only resolved fills contribute to result fractions.",
            "OHLC cannot reconstruct fills, spreads, price-band queues, liquidity or corporate actions.",
            "Historical replay tests a fixed-quantity signal and fill model; portfolio limits and dated tradeability need separate validation.",
            "Rule replay covers supplied candles and universe; these may have selection and survivorship bias.",
        ],
    }
