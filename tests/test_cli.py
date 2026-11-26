import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from india_trader.cli import main
from india_trader.data import fetch_upstox
from india_trader.events import fetch_headlines
from india_trader.models import load_calendar, timestamp


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.start = '2026-11-26T09:35:00+05:30'
        self.end = '2026-11-26T09:45:00+05:30'

    def invoke(self, *args):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = main(list(map(str, args)))
        self.assertEqual(status, 0, err.getvalue())
        return json.loads(out.getvalue())

    def test_paper_demo_and_alert_deduplication(self):
        self.invoke('demo', '--output', self.folder)
        candles = ('--bars', self.folder / 'bars.csv')
        account = ('--db', self.folder / 'ledger.sqlite3')
        evidence = ('--instruments', self.folder / 'instruments.csv')
        preview = self.invoke('ticket', 'DEMO', *candles, *account, *evidence, '--benchmark', 'NIFTY50',
                              '--asof', self.start, '--target-r', '2.5')['ticket']
        self.assertEqual((preview['quantity'], preview['stop'], preview['target']), (49, 500.5, 516.25))
        self.assertEqual(self.invoke('paper-advance', 'DEMO', *candles, *account,
                                     '--asof', self.start)['events'], [])
        queued = self.invoke('paper-confirm', 'DEMO', *candles, *account, *evidence,
                             '--digest', preview['digest'], '--asof', self.start)
        self.assertEqual(queued['state'], 'PENDING')
        closed, = self.invoke('paper-advance', 'DEMO', *candles, *account, '--asof', self.end)['events']
        self.assertEqual((closed['state'], closed['exit_reason'], closed['net_pnl']), ('CLOSED', 'TARGET', 495.20))
        rules = ('--rules', self.folder / 'alerts.json', '--db', self.folder / 'alerts.sqlite3')
        first = self.invoke('alerts', *candles, *rules, '--asof', self.end)['new_alerts']
        self.assertEqual([item['id'] for item in first], ['demo-target'])
        self.assertEqual(self.invoke('alerts', *candles, *rules, '--asof', self.end)['new_alerts'], [])

    def test_replay_and_filings_use_the_requested_time(self):
        self.invoke('demo', '--output', self.folder)
        report = self.invoke('replay', 'DEMO', '--bars', self.folder / 'bars.csv', '--benchmark', 'NIFTY50',
                             '--asof', self.start, '--current', '--target-r', '2.5', '--simulations', '20')
        self.assertEqual(report['bootstrap']['requested_draws'], 20)
        self.assertLess(0, report['history']['resolved_fills'])
        disclosure, = self.invoke('disclosures', '--events', self.folder / 'events.csv', '--symbol', 'DEMO',
                                  '--asof', self.start)['recent']
        self.assertEqual(disclosure['filing_lag_hours'], 3)
        report = self.invoke('earnings', 'DEMO', '--results', self.folder / 'earnings.csv', '--asof', self.start)
        self.assertAlmostEqual(report['metrics']['revenue']['yoy_pct'], (126 / 110 - 1) * 100)

    def test_dated_market_calendar(self):
        cal = load_calendar()
        self.assertNotIn(date(2026, 1, 15), cal.sessions)
        self.assertIn(date(2026, 2, 1), cal.sessions)
        self.assertNotIn(date(2026, 11, 8), cal.sessions)
        self.assertEqual(cal.sessions[date(2026, 11, 26)].closes.strftime('%H:%M'), '15:15')
        self.assertEqual(cal.sessions[date(2026, 12, 7)].closes.strftime('%H:%M'), '15:30')
        with self.assertRaisesRegex(ValueError, 'outside'):
            cal.require_coverage(date(2027, 1, 1))


class ProviderTests(unittest.TestCase):
    def test_historical_get_path_and_forming_candle_exclusion(self):
        def serve(request, timeout):
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(request.get_header('Authorization'), 'Bearer sample-token')
            self.assertEqual(request.full_url, 'https://api.upstox.com/v3/historical-candle/'
                             'NSE_EQ%7CINE000D01001/minutes/5/2026-11-26/2026-11-25')
            payload = {'status': 'success', 'data': {'candles': [
                ['2026-11-26T09:30:00+05:30', 100, 102, 99, 101, 12000, 0],
                ['2026-11-26T09:25:00+05:30', 98, 101, 97, 100, 11000, 0],
            ]}}
            return BytesIO(json.dumps(payload).encode())

        bars = fetch_upstox('DEMO', 'NSE_EQ|INE000D01001', 'sample-token', date(2026, 11, 25), date(2026, 11, 26),
                            opener=serve, requested_at=timestamp('2026-11-26T09:34:59+05:30'))
        self.assertEqual([bar.ts.minute for bar in bars], [25])

    def test_headlines_use_only_selected_keys_and_publication_cutoff(self):
        asof = timestamp('2026-11-26T09:35:00+05:30')
        key = 'NSE_EQ|INE000D01001'

        def serve(request, timeout):
            self.assertIn('instrument_keys=NSE_EQ%7CINE000D01001', request.full_url)
            current = int(asof.timestamp() * 1000)
            rows = [{'heading': heading, 'article_link': 'https://example.com/article', 'published_time': stamp}
                    for heading, stamp in [('available', current), ('future', current + 1000)]]
            return BytesIO(json.dumps({'status': 'success', 'data': {key: rows}}).encode())

        report = fetch_headlines([key], 'sample-token', asof, opener=serve)
        self.assertEqual([item['title'] for item in report['headlines']], ['available'])
