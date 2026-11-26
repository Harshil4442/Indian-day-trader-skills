"""Command-line workflows for the India Day Trader skill."""

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path

from .data import fetch_upstox, instruments_at, read_bars, read_universe, write_bars
from .events import earnings_at, events_at, fetch_headlines
from .models import DEFAULT_CALENDAR, FIVE_MINUTES, IST, load_calendar, symbol_name, timestamp
from .risk import Costs, RiskConfig, make_plan
from .signals import SETUPS, SignalRules, analyze, premarket


def _clock(value=None):
    return timestamp(value) if value else datetime.now(IST)


def _json_value(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if is_dataclass(obj):
        return asdict(obj)
    raise TypeError(f'cannot encode {type(obj).__name__}')


def _config(path):
    cfg = json.loads(path.read_text(encoding='utf-8')) if path else {}
    if not isinstance(cfg, dict) or cfg.keys() - {'signals', 'risk'}:
        raise ValueError('config expects signals and risk objects')
    signal_values = cfg.get('signals', {})
    risk_values = dict(cfg.get('risk', {}))
    costs = Costs(**risk_values.pop('costs', {}))
    rules = SignalRules(**signal_values)
    if rules.interval_min != 5:
        raise ValueError('the CLI expects five-minute candles')
    return rules, RiskConfig(**risk_values, costs=costs)


def _bar_inputs(args):
    return read_bars(args.bars)


def _select(by_symbol, symbol):
    if symbol not in by_symbol:
        raise ValueError(f'no candle data supplied for {symbol}')
    return by_symbol[symbol]


def _benchmark(args, by_symbol):
    return _select(by_symbol, args.benchmark) if args.benchmark else ()


def _instrument(args, now):
    snapshot = instruments_at(args.instruments, now).get(args.symbol)
    if snapshot is None:
        raise ValueError(f'no instrument snapshot at this time for {args.symbol}')
    return snapshot


def _regular(calendar, now):
    session = calendar.sessions.get(now.date())
    if session is None:
        raise ValueError(f'no regular session on {now.date()}: {calendar.status(now.date())}')
    return session


def _current_bar(bars, session, now):
    slots = (now - session.opens) // FIVE_MINUTES
    start = session.opens + (slots - 1) * FIVE_MINUTES
    for bar in reversed(bars):
        if bar.ts == start and session.contains_bar(bar.ts):
            return bar
    raise ValueError(f'latest completed candle unavailable at {start.isoformat()}')


def _signal_plan(report, setup, target_r):
    for signal in report['signals']:
        if signal['setup'] == setup:
            return make_plan(signal['symbol'], signal['setup'], timestamp(signal['signal_time']),
                             signal['entry'], signal['stop'], target_r)
    outcome = report['setups'].get(setup, {})
    reasons = outcome.get('reasons') or report['reasons']
    raise ValueError(f"{setup} has no fresh confirmation ({outcome.get('status', report['status'])}): "
                     f"{', '.join(reasons) or 'no matching pattern'}")


def _scan(args, by_symbol, calendar, now, rules, benchmark):
    universe = (read_universe(args.universe) if args.universe else
                {symbol: {'symbol': symbol} for symbol in (args.symbol or sorted(by_symbol))})
    excluded = []
    if args.symbol and args.universe:
        excluded = sorted(set(args.symbol) - universe.keys())
        universe = {symbol: universe[symbol] for symbol in args.symbol if symbol in universe}
    symbols = [symbol for symbol in universe if symbol != args.benchmark]
    unknown = [symbol for symbol in symbols if symbol not in by_symbol]
    scan = premarket if args.command == 'premarket' else analyze
    options = {'benchmark': benchmark} if args.command == 'scan' else {}
    instruments = instruments_at(args.instruments, now) if args.instruments else {}
    reports = []
    for symbol in symbols:
        if symbol in by_symbol:
            report = scan(by_symbol[symbol], calendar.sessions, now, rules=rules, symbol=symbol, **options)
            report['universe'] = universe[symbol]
            if args.instruments:
                report['instrument'] = instruments.get(symbol)
            reports.append(report)
    if args.command == 'scan':
        reports.sort(key=lambda row: (-len(row['signals']),
                                      -(row['metrics'].get('bar_rvol') or 0), row['symbol']))
    result = {'asof': now, 'calendar': calendar.calendar_id, 'requested': len(symbols),
              'received': len(reports), 'missing': unknown, 'excluded_by_universe': excluded,
              'benchmark': args.benchmark, 'reports': reports}
    if args.command == 'scan':
        returns = [row['metrics']['session_return_bps'] for row in reports if 'session_return_bps' in row['metrics']]
        result['universe_breadth_since_open'] = {'observed': len(returns), 'universe_size': len(symbols),
                                                'advancing': sum(value > 0 for value in returns),
                                                'declining': sum(value < 0 for value in returns),
                                                'unchanged': sum(value == 0 for value in returns)}
    return result


def _run(args):
    if args.command == 'demo':
        from .demo import create_demo

        return create_demo(args.output)
    if args.command == 'universe':
        universe = read_universe(args.file)
        return {'input': str(args.file), 'count': len(universe), 'members': list(universe.values())}
    if args.command == 'fetch-bars':
        bars = fetch_upstox(args.symbol, args.key, os.environ.get('UPSTOX_ACCESS_TOKEN', ''),
                            args.from_date, args.to_date)
        write_bars(args.output, bars, 'Upstox V3 market data')
        return {'output': str(args.output), 'source': 'Upstox V3', 'symbol': args.symbol,
                'candles': len(bars), 'first_start': bars[0].ts, 'last_start': bars[-1].ts}

    now = _clock(args.asof)
    if args.command == 'headlines':
        return fetch_headlines(args.key, os.environ.get('UPSTOX_ACCESS_TOKEN', ''), now, page=args.page)
    if args.command in {'news', 'disclosures'}:
        return events_at(args.events, now, symbols=set(args.symbol or ()), hours=args.hours,
                         forward_hours=args.forward_hours, disclosures_only=args.command == 'disclosures')
    if args.command == 'earnings':
        return earnings_at(args.results, args.symbol, now, args.basis)
    calendar = load_calendar(args.calendar)
    calendar.require_coverage(now.date())
    if args.command == 'calendar':
        return {'asof': now, 'calendar': calendar.calendar_id, 'status': calendar.status(now.date()),
                'session': calendar.sessions.get(now.date()), 'coverage': [calendar.start, calendar.end]}
    if args.command == 'alerts':
        from .alerts import check_alerts

        return check_alerts(args.rules, args.db, _bar_inputs(args), calendar, now)

    rules, risk = _config(args.config)
    if args.command in {'paper-status', 'paper-reconcile', 'paper-cancel'}:
        from .paper import PaperLedger

        ledger = PaperLedger(args.db, risk)
        if args.command == 'paper-status':
            return ledger.status(now.date())
        if args.command == 'paper-cancel':
            return ledger.cancel(args.digest, now)
        return ledger.reconcile(args.digest, args.exit_price, now, args.note)
    by_symbol = _bar_inputs(args)
    benchmark = _benchmark(args, by_symbol)
    if args.command in {'scan', 'premarket'}:
        report = _scan(args, by_symbol, calendar, now, rules, benchmark)
        report['candle_inputs'] = [str(path) for path in args.bars]
        return report
    bars = _select(by_symbol, args.symbol)
    if args.command == 'replay':
        from .replay import replay

        plan = None
        if args.current:
            report = analyze(bars, calendar.sessions, now, benchmark=benchmark, rules=rules, symbol=args.symbol)
            planned = _signal_plan(report, args.setup, args.target_r)
            plan = {'entry': planned.entry, 'stop': planned.stop, 'target': planned.target,
                    'signal_time': planned.signal_at.isoformat()}
        return replay(bars, calendar.sessions, now, benchmark=benchmark, rules=rules, symbol=args.symbol,
                      setup=args.setup, costs=risk.costs, tick_size=args.tick_size, quantity=args.quantity,
                      target_r=args.target_r, entry_before=risk.entry_deadline, flat_by=risk.square_off, flat_buffer_min=5,
                      max_entry_drift_bps=risk.max_entry_drift_bps, simulations=args.simulations,
                      seed=args.seed, plan=plan)
    if args.command == 'paper-advance':
        from .paper import PaperLedger

        regular = [bar for bar in bars if (session := calendar.sessions.get(bar.ts.date()))
                   and session.contains_bar(bar.ts)]
        return {'symbol': args.symbol, 'asof': now,
                'events': PaperLedger(args.db, risk).advance(args.symbol, regular, now)}
    if args.command == 'paper-confirm':
        from .paper import PaperLedger

        session = _regular(calendar, now)
        return PaperLedger(args.db, risk).confirm(args.digest, _instrument(args, now), session,
                                                   _current_bar(bars, session, now), now)
    report = analyze(bars, calendar.sessions, now, benchmark=benchmark, rules=rules, symbol=args.symbol)
    if args.command == 'entry':
        report['candle_inputs'] = [str(path) for path in args.bars]
        report['calendar'] = calendar.calendar_id
        return report
    plan = _signal_plan(report, args.setup, args.target_r)
    if args.command == 'plan':
        return {'status': 'research plan', 'plan': plan,
                'flat_by_model': risk.square_off, 'market': report['metrics']}

    from .paper import PaperLedger

    session = _regular(calendar, now)
    ticket = PaperLedger(args.db, risk).preview(plan, _instrument(args, now), session,
                                                 _current_bar(bars, session, now), now)
    return {'status': 'paper preview', 'ticket': ticket}


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calendar', type=Path, default=DEFAULT_CALENDAR)
    parser.add_argument('--config', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)

    def command(name, description, *, clock=True, bars=False, market=False, symbol=False,
                setup=False, db=False, instruments=False):
        sub = commands.add_parser(name, help=description, description=description)
        if clock:
            sub.add_argument('--asof', help='ISO-8601 information cutoff with a UTC offset (default current IST)')
        if bars:
            sub.add_argument('--bars', type=Path, action='append', required=True, help='5-minute CSV, repeat for more files')
        if market:
            sub.add_argument('--benchmark', type=symbol_name, help='index alias from candle files, e.g. NIFTY50')
        if symbol:
            sub.add_argument('symbol', type=symbol_name)
        if setup:
            sub.add_argument('--setup', choices=SETUPS, default='orb')
            sub.add_argument('--target-r', type=float, default=2.0)
        if db:
            sub.add_argument('--db', type=Path, default=Path('state/paper.sqlite3'))
        if instruments:
            sub.add_argument('--instruments', type=Path, required=True)
        return sub

    command('demo', 'Generate reproducible synthetic data', clock=False).add_argument(
        '--output', type=Path, default=Path('state/demo'))
    command('universe', 'Read an NSE Indices universe CSV or watchlist', clock=False).add_argument('file', type=Path)
    fetch = command('fetch-bars', 'Fetch Upstox NSE cash or index candles with a read-only request', clock=False, symbol=True)
    fetch.add_argument('--key', required=True)
    fetch.add_argument('--from-date', type=date.fromisoformat)
    fetch.add_argument('--to-date', type=date.fromisoformat)
    fetch.add_argument('--output', type=Path, required=True)
    command('calendar', 'Show the NSE regular session model for an IST date')
    for name, description in [('premarket', 'Review completed daily liquidity and price structure'),
                              ('scan', 'Scan an NSE cash universe for closed-bar setups')]:
        scan = command(name, description, bars=True, market=True)
        scan.add_argument('--universe', type=Path)
        scan.add_argument('--symbol', type=symbol_name, action='append')
        scan.add_argument('--instruments', type=Path)
    command('entry', 'Explain which triggers are waiting, blocked or newly confirmed',
            bars=True, market=True, symbol=True)
    command('plan', 'Create a structural stop and target from a fresh signal',
            bars=True, market=True, symbol=True, setup=True)
    replay = command('replay', 'Replay historical signals and optionally resample a fresh plan',
                     bars=True, market=True, symbol=True, setup=True)
    replay.add_argument('--current', action='store_true')
    replay.add_argument('--tick-size', type=float, default=0.05, help='historical research input, verify for the instrument')
    replay.add_argument('--quantity', type=int, default=1)
    replay.add_argument('--simulations', type=int, default=1000)
    replay.add_argument('--seed', type=int, default=19)
    command('ticket', 'Size, risk-check and store an exact paper order preview',
            bars=True, market=True, symbol=True, setup=True, db=True, instruments=True)
    confirm = command('paper-confirm', 'Confirm one exact paper order preview', bars=True, market=True,
                      symbol=True, db=True, instruments=True)
    confirm.add_argument('--digest', required=True)
    command('paper-advance', 'Apply subsequent complete candles to the paper ledger',
            bars=True, market=True, symbol=True, db=True)
    command('paper-status', 'View paper cash, exposure, order history and P&L', db=True)
    command('paper-cancel', 'Cancel a pending simulated entry', db=True).add_argument('--digest', required=True)
    reconcile = command('paper-reconcile', 'Record a manually evidenced simulated exit', db=True)
    reconcile.add_argument('--digest', required=True)
    reconcile.add_argument('--exit-price', type=float, required=True)
    reconcile.add_argument('--note', required=True)
    alerts = command('alerts', 'Emit unseen candle and clock alerts', bars=True, db=True)
    alerts.set_defaults(db=Path('state/alerts.sqlite3'))
    alerts.add_argument('--rules', type=Path, required=True)
    headlines = command('headlines', 'Fetch recent market headlines from the Upstox News API')
    headlines.add_argument('--key', required=True, action='append', help='NSE_EQ key, repeat for more instruments')
    headlines.add_argument('--page', type=int, default=1)
    for name in ('news', 'disclosures'):
        events = command(name, 'Inspect point-in-time announcements and upcoming events' if name == 'news'
                         else 'Inspect bulk, block, insider and shareholding disclosures')
        events.add_argument('--events', type=Path, required=True)
        events.add_argument('--symbol', type=symbol_name, action='append')
        events.add_argument('--hours', type=int, default=48)
        events.add_argument('--forward-hours', type=int, default=24)
    earnings = command('earnings', 'Compare reported quarterly facts like for like', symbol=True)
    earnings.add_argument('--results', type=Path, required=True)
    earnings.add_argument('--basis', choices=('consolidated', 'standalone'), default='consolidated')
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        result = _run(args)
        print(json.dumps(result, default=_json_value, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        error = {'error': str(exc)}
        if hasattr(exc, 'reasons'):
            error['reasons'] = exc.reasons
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return 2
