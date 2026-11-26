import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta

from india_trader.models import Bar, IST, Session
from india_trader.replay import replay
from india_trader.signals import SignalRules, analyze, premarket


SYMBOL = "DEMOEQ"
STEP = timedelta(minutes=5)
RULES = SignalRules(
    min_baseline_sessions=2, min_median_daily_turnover_inr=0,
    min_cumulative_rvol=1.1, min_bar_rvol=1.2,
    latest_entry="10:00", last_entry_buffer_min=10,
)


class TestCosts:
    def buy_price(self, price, tick_size):
        return price

    def sell_price(self, price, tick_size):
        return price

    def round_trip(self, entry, exit, quantity):
        return 0.5


def at(day, hour, minute, second=0):
    return datetime.combine(day, time(hour, minute, second), IST)


def fixture_data():
    dates = [date(2026, 3, 9) + timedelta(days=index) for index in range(5)]
    sessions = {day: Session(day, at(day, 9, 15), at(day, 10, 15)) for day in dates}
    by_day = {}
    for day in dates:
        bars = [
            Bar(SYMBOL, sessions[day].opens + index * STEP,
                100.0, 100.45, 99.8, 100.0, 1_000)
            for index in range(12)
        ]
        by_day[day] = bars
    for day in dates[2:]:
        bars = by_day[day]
        bars[3] = replace(bars[3], close=101.0, high=101.1, volume=2_500)
        bars[4] = replace(bars[4], open=101.1, close=101.3, high=104.0, low=99.0)
    return dates, sessions, by_day


def flattened(by_day):
    return [bar for day in by_day.values() for bar in day]


class SignalTests(unittest.TestCase):
    def setUp(self):
        self.dates, self.sessions, self.by_day = fixture_data()
        self.day = self.dates[-1]

    def analyze(self, hour, minute, second=0, **kwargs):
        return analyze(
            flattened(self.by_day), self.sessions, at(self.day, hour, minute, second),
            rules=RULES, **kwargs,
        )

    def test_open_candle_cannot_trigger_and_next_closed_candle_can(self):
        waiting = self.analyze(9, 34, 59)
        self.assertEqual(waiting["status"], "waiting")
        at_close = self.analyze(9, 35)
        self.assertEqual(at_close["status"], "triggered")
        hit = at_close["signals"][0]
        self.assertEqual(hit["setup"], "orb")
        self.assertEqual(hit["signal_time"], at(self.day, 9, 35).isoformat())
        self.assertAlmostEqual(hit["stop"], 100.125)
        self.assertAlmostEqual(at_close["metrics"]["bar_rvol"], 2_500 / 1_750)

    def test_future_and_missing_candles_are_treated_differently(self):
        expected = self.analyze(9, 35)
        self.by_day[self.day][4] = replace(self.by_day[self.day][4], high=5000, close=4999)
        self.assertEqual(expected, self.analyze(9, 35))
        self.by_day[self.day].pop(1)
        self.assertEqual(self.analyze(9, 35)["reasons"], ["missing_or_stale_closed_candles"])

    def test_previous_trigger_is_visible_but_not_fresh(self):
        self.assertEqual(self.analyze(9, 35)["setups"]["orb"]["status"], "triggered")
        later = self.analyze(9, 45)
        self.assertEqual(later["setups"]["orb"]["status"], "already_triggered")
        self.assertFalse(any(hit["setup"] == "orb" for hit in later["signals"]))

    def test_weak_and_stale_benchmark_blocks_confirmation(self):
        candles = [
            replace(bar, symbol="NIFTY50", open=25000, high=25005, low=24800,
                    close=24830, volume=0)
            for bar in self.by_day[self.day][:4]
        ]
        weak = self.analyze(9, 35, benchmark=candles)
        self.assertIn("benchmark_below_minimum", weak["setups"]["orb"]["reasons"])
        self.assertEqual(self.analyze(9, 35, benchmark=candles[:-1])["reasons"],
                         ["missing_or_stale_benchmark_candles"])

    def test_vwap_reclaim_needs_a_close_through_vwap_and_prior_high(self):
        bars = self.by_day[self.day]
        bars[3] = replace(bars[3], high=100.2, low=99.8, close=99.9, volume=1_000)
        bars[4] = replace(bars[4], open=100, high=100.35, low=99.95, close=100.3, volume=2_500)
        hits = self.analyze(9, 40)["signals"]
        self.assertEqual([hit["setup"] for hit in hits], ["vwap-reclaim"])
        self.assertAlmostEqual(hits[0]["stop"], 99.8)

    def test_trend_pullback_confirms_after_price_resumes_above_support(self):
        bars = self.by_day[self.day]
        bars[4] = replace(bars[4], open=101, high=101.1, low=100.6, close=100.8)
        bars[5] = replace(bars[5], open=100.8, high=101.05, low=100.7, close=100.9)
        bars[6] = replace(bars[6], open=100.9, high=101.3, low=100.85, close=101.25, volume=2_500)
        hits = self.analyze(9, 50)["signals"]
        self.assertEqual([hit["setup"] for hit in hits], ["trend-pullback"])
        self.assertAlmostEqual(hits[0]["stop"], 100.6)

    def test_premarket_only_uses_completed_sessions(self):
        snapshot = premarket(
            flattened(self.by_day), self.sessions, at(self.day, 9, 35), rules=RULES,
        )
        self.assertEqual(snapshot["latest_modeled_session"], self.dates[-2].isoformat())
        self.assertEqual(snapshot["last_modeled_close"], 100.0)
        self.assertNotEqual(snapshot["last_modeled_session_high"],
                            max(bar.high for bar in self.by_day[self.day][:4]))
        self.by_day[self.dates[-2]].pop()
        snapshot = premarket(
            flattened(self.by_day), self.sessions, at(self.day, 9, 35), rules=RULES,
        )
        self.assertEqual(snapshot["reasons"], ["latest_completed_session_missing_candles"])


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.dates, self.sessions, self.by_day = fixture_data()
        self.day = self.dates[-1]

    def run_replay(self, **kwargs):
        return replay(
            flattened(self.by_day), self.sessions, at(self.day, 9, 35), rules=RULES,
            costs=TestCosts(), flat_by="10:10", flat_buffer_min=5, **kwargs,
        )

    def test_replay_uses_next_open_and_adverse_same_bar_order(self):
        history = self.run_replay()["history"]
        self.assertEqual(history["outcome_counts"], {"insufficient_prior_baseline": 2, "stop": 2})
        row = history["sessions"][2]
        self.assertEqual(row["signal_time"], at(self.dates[2], 9, 35).isoformat())
        self.assertAlmostEqual(row["entry_reference"], 101.0)
        self.assertAlmostEqual(row["entry_fill"], 101.1)
        self.assertAlmostEqual(row["exit_fill"], 100.1)
        self.assertAlmostEqual(row["net_inr"], -1.5)
        self.assertEqual(history["sessions"][-1]["date"], self.dates[-2].isoformat())

    def test_replay_prices_stop_gap_at_open_and_marks_missing_tail(self):
        first, second = self.dates[2:4]
        for day in (first, second):
            self.by_day[day][4] = replace(self.by_day[day][4], low=100.9, high=101.4)
        self.by_day[first][5] = replace(
            self.by_day[first][5], open=99.2, low=99.1, high=100.2, close=100.0,
        )
        self.by_day[second].pop(5)
        history = self.run_replay()["history"]
        self.assertEqual(history["sessions"][2]["exit_fill"], 99.2)
        self.assertEqual(history["sessions"][3]["outcome"], "unresolved_after_entry")
        self.assertEqual(history["resolved_fills"], 1)
        self.assertEqual(history["unresolved_fills"], 1)

    def test_bootstrap_reports_sample_size_and_is_repeatable(self):
        plan = {"symbol": SYMBOL, "setup": "orb", "signal_time": at(self.day, 9, 35).isoformat(),
                "entry": 101.0, "stop": 100.1, "target": 102.8}
        sample = self.run_replay(plan=plan, simulations=10, min_sim_sessions=2, seed=14)
        repetition = self.run_replay(plan=plan, simulations=10, min_sim_sessions=2, seed=14)
        self.assertEqual(sample, repetition)
        bootstrap = sample["bootstrap"]
        self.assertEqual(bootstrap["matched_sessions"], 4)
        self.assertEqual(bootstrap["observations"], 10)
        self.assertEqual(self.run_replay(plan=plan)["bootstrap"]["status"],
                         "insufficient_matched_sessions")


if __name__ == "__main__":
    unittest.main()
