import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from india_trader.models import Bar, Instrument, IST, Session
from india_trader.paper import PaperLedger
from india_trader.risk import Costs, RiskBlocked, RiskConfig, make_plan


DAY = date(2026, 11, 25)
DAY_AFTER = date(2026, 11, 26)


def at(hour, minute, *, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def session(day=DAY):
    return Session(day, at(9, 15, day=day), at(15, 15, day=day))


def instrument(symbol="DEMO", *, day=DAY):
    return Instrument(
        symbol, isin="INE000D01001", tick_size=0.05, lower_band=90.0, upper_band=110.0, status="clear",
        asof=at(9, 0, day=day), source="synthetic fixture",
    )


def bar(hour, minute, opening=100.0, high=100.5, low=99.5, close=100.0, *,
        symbol="DEMO", volume=10_000, day=DAY):
    return Bar(symbol, at(hour, minute, day=day), opening, high, low, close, volume)


def plan(symbol="DEMO", *, day=DAY, signal_at=None):
    return make_plan(symbol, "orb", signal_at or at(10, 0, day=day), 100.0, 99.0, target_r=3.0)


class RiskPaperTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "paper.sqlite3"
        self.config = RiskConfig()
        self.ledger = PaperLedger(self.path, self.config)

    def preview(self, symbol="DEMO", *, ledger=None):
        return (ledger or self.ledger).preview(
            plan(symbol), instrument(symbol), session(), bar(9, 55, symbol=symbol), at(10, 0),
        )

    def approve(self, ticket=None, *, ledger=None):
        ticket = ticket or self.preview(ledger=ledger)
        symbol = ticket.plan.symbol
        return (ledger or self.ledger).confirm(
            ticket.digest, instrument(symbol), session(), bar(9, 55, symbol=symbol), at(10, 0),
        )

    def test_estimated_cash_equity_costs_and_tick_slippage(self):
        costs = Costs()
        self.assertEqual(costs.round_trip(100.0, 105.0, 500), 54.75)
        self.assertEqual(costs.buy_price(100.01, 0.05), 100.1)
        self.assertEqual(costs.sell_price(100.01, 0.05), 99.95)

    def test_preview_is_sized_at_worst_permitted_price_after_charges(self):
        ticket = self.preview()
        self.assertGreater(ticket.quantity, 0)
        self.assertLessEqual(ticket.maximum_cash, self.config.initial_cash * self.config.max_position_fraction)
        self.assertLessEqual(ticket.planned_loss, self.config.max_trade_risk)
        self.assertGreaterEqual(ticket.net_reward_risk, self.config.min_net_rr)
        self.assertGreater(ticket.maximum_entry, ticket.expected_entry)
        self.assertEqual(self.ledger.inspect_ticket(ticket.digest)["quantity"], ticket.quantity)
        self.assertEqual(self.ledger.status(DAY)["orders"], [])

    def test_pre_trade_restriction_metadata_and_candle_checks(self):
        for changes, expected in (
            ({"status": "asm"}, "INSTRUMENT_RESTRICTED_OR_UNVERIFIED"),
            ({"asof": at(9, 0, day=DAY_AFTER)}, "INSTRUMENT_EVIDENCE_STALE_OR_FUTURE"),
            ({"series": "BE"}, "INELIGIBLE_INSTRUMENT"),
            ({"upper_band": 101.0}, "PLAN_OUTSIDE_BANDS_OR_COLLAPSES_AFTER_COSTS"),
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(RiskBlocked, expected):
                self.ledger.preview(plan(), replace(instrument(), **changes), session(), bar(9, 55), at(10, 0))
        with self.assertRaisesRegex(RiskBlocked, "STALE_OR_INVALID_CANDLE"):
            self.ledger.preview(plan(), instrument(), session(), bar(10, 0), at(10, 0))
        with self.assertRaisesRegex(RiskBlocked, "ENTRY_CUTOFF"):
            self.ledger.preview(
                plan(signal_at=at(14, 40)), instrument(), session(), bar(14, 35), at(14, 42),
            )

    def test_confirmation_expiry_idempotence_and_no_unconfirmed_fill(self):
        ticket = self.preview()
        self.assertEqual(self.ledger.advance("DEMO", [bar(10, 0)], at(10, 5)), [])
        with self.assertRaisesRegex(RiskBlocked, "TICKET_EXPIRED_OR_NOT_YET_VALID"):
            self.ledger.confirm(ticket.digest, instrument(), session(), bar(10, 0), at(10, 5))
        pending = self.approve(ticket)
        self.assertEqual(pending["state"], "PENDING")
        self.assertEqual(pending, PaperLedger(self.path, self.config).confirm(
            ticket.digest, instrument(), session(), bar(10, 0), at(10, 5),
        ))
        self.assertEqual(self.ledger.advance("DEMO", [bar(10, 0)], at(10, 4)), [])

    def test_confirmation_rechecks_shared_position_capacity(self):
        config = replace(self.config, max_open_positions=1)
        ledger = PaperLedger(Path(self.temp.name) / "race.sqlite3", config)
        tickets = [self.preview(symbol, ledger=ledger) for symbol in ("ALPHA", "BETA")]

        def attempt(ticket):
            try:
                return self.approve(ticket, ledger=ledger)["state"]
            except RiskBlocked as blocked:
                return blocked.reason

        with ThreadPoolExecutor(max_workers=2) as callers:
            results = list(callers.map(attempt, tickets))
        self.assertCountEqual(results, ["PENDING", "OPEN_POSITION_LIMIT"])
        self.assertEqual(len(ledger.status(DAY)["orders"]), 1)

    def test_same_bar_both_levels_takes_stop_first_and_accounts_for_costs(self):
        ticket = self.preview()
        self.approve(ticket)
        results = self.ledger.advance("DEMO", [bar(10, 0, high=104.0, low=98.5)], at(10, 5))
        closed = results[0]
        self.assertEqual(closed["state"], "CLOSED")
        self.assertEqual(closed["entry_at"], at(10, 0).isoformat())
        self.assertEqual(closed["exit_reason"], "STOP")
        self.assertEqual(closed["exit_price"], ticket.estimated_stop_fill)
        self.assertGreater(closed["fees"], 0)
        self.assertLess(closed["net_pnl"], 0)
        self.assertEqual(self.ledger.status(DAY)["account"]["realized_today"], closed["net_pnl"])
        self.assertEqual(self.ledger.advance("DEMO", [bar(10, 0)], at(10, 5)), [])

    def test_first_open_outside_entry_cap_cancels_without_a_trade(self):
        self.approve()
        [canceled] = self.ledger.advance(
            "DEMO", [bar(10, 0, opening=101.0, high=101.5, low=99.5, close=100.0)], at(10, 5),
        )
        self.assertEqual(canceled["state"], "CANCELED")
        self.assertIsNone(canceled["entry_at"])
        self.assertEqual(self.ledger.status(DAY)["account"]["trades_today"], 0)

    def test_daily_loss_uses_realized_gap_loss(self):
        self.approve()
        [closed] = self.ledger.advance(
            "DEMO",
            [bar(10, 0), bar(10, 5, opening=91.0, high=95.0, low=90.0, close=92.0)],
            at(10, 10),
        )
        self.assertLess(closed["net_pnl"], -self.config.max_daily_loss)
        candidate = plan("OTHER", signal_at=at(10, 10))
        with self.assertRaisesRegex(RiskBlocked, "DAILY_LOSS_LIMIT"):
            self.ledger.preview(
                candidate, instrument("OTHER"), session(), bar(10, 5, symbol="OTHER"), at(10, 10),
            )

    def test_missing_bar_keeps_prior_exposure_and_can_be_repaired(self):
        self.approve()
        later = bar(10, 10, high=103.5)
        [order] = self.ledger.advance("DEMO", [bar(10, 0), later], at(10, 15))
        self.assertEqual(order["state"], "OPEN")
        self.assertTrue(order["note"].startswith("MISSING_COMPLETED_BAR"))
        self.assertTrue(self.ledger.status(DAY_AFTER)["account"]["prior_session_exposure"])
        with self.assertRaisesRegex(RiskBlocked, "UNRESOLVED_PRIOR_SESSION"):
            self.ledger.preview(
                plan("OTHER", day=DAY_AFTER), instrument("OTHER", day=DAY_AFTER),
                session(DAY_AFTER), bar(9, 55, symbol="OTHER", day=DAY_AFTER),
                at(10, 0, day=DAY_AFTER),
            )
        [repaired] = self.ledger.advance("DEMO", [bar(10, 5), later], at(10, 15))
        self.assertEqual(repaired["exit_reason"], "TARGET")
        self.assertFalse(self.ledger.status(DAY_AFTER)["account"]["prior_session_exposure"])

    def test_band_locked_stop_waits_for_liquidity(self):
        self.approve()
        band_locked = bar(10, 5, opening=90.0, high=90.0, low=90.0, close=90.0)
        [locked] = self.ledger.advance("DEMO", [bar(10, 0), band_locked], at(10, 10))
        self.assertEqual(locked["state"], "OPEN")
        self.assertEqual(locked["note"], "STOP_EXIT_UNFILLED")
        [closed] = self.ledger.advance(
            "DEMO", [bar(10, 10, opening=91.0, high=94.0, low=90.0, close=93.0)], at(10, 15),
        )
        self.assertEqual(closed["exit_reason"], "DEFERRED_STOP")
        self.assertLess(closed["exit_price"], 91.0)

    def test_missing_entry_bar_can_be_canceled_without_erasing_a_fill(self):
        ticket = self.preview()
        self.approve(ticket)
        [pending] = self.ledger.advance("DEMO", [], at(10, 5))
        self.assertEqual(pending["state"], "PENDING")
        canceled = self.ledger.cancel(ticket.digest, at(10, 0, day=DAY_AFTER))
        self.assertEqual((canceled["state"], canceled["exit_reason"]), ("CANCELED", "MANUAL_PAPER_CANCEL"))
        self.assertIsNone(canceled["entry_at"])
        self.assertEqual(self.ledger.cancel(ticket.digest, at(10, 5, day=DAY_AFTER)), canceled)
        self.assertFalse(self.ledger.status(DAY_AFTER)["account"]["prior_session_exposure"])
        self.assertEqual(self.ledger.advance("DEMO", [bar(10, 0)], at(10, 5)), [])

    def test_filled_order_cannot_be_canceled(self):
        ticket = self.preview()
        self.approve(ticket)
        self.ledger.advance("DEMO", [bar(10, 0)], at(10, 5))
        with self.assertRaisesRegex(RiskBlocked, "CANNOT_CANCEL_PAPER_FILL"):
            self.ledger.cancel(ticket.digest, at(10, 5))

    def test_squareoff_without_a_liquid_bar_leaves_open_review(self):
        ticket = self.ledger.preview(
            plan(signal_at=at(14, 40)), instrument(), session(), bar(14, 35), at(14, 40),
        )
        self.ledger.confirm(ticket.digest, instrument(), session(), bar(14, 35), at(14, 40))
        [locked] = self.ledger.advance(
            "DEMO",
            [bar(14, 40), bar(14, 45), bar(14, 50), bar(14, 55), bar(15, 0, volume=0)],
            at(15, 5),
        )
        self.assertEqual((locked["state"], locked["note"]), ("OPEN", "FLATTEN_UNFILLED"))
        [position] = self.ledger.status(DAY_AFTER)["orders"]
        self.assertEqual(position["needs_review"], "UNRESOLVED_PRIOR_SESSION")
        reconciled = self.ledger.reconcile(ticket.digest, 99.5, at(15, 10), "Broker-export paper fill")
        self.assertEqual(reconciled["exit_reason"], "MANUAL_PAPER_RECONCILIATION")
        self.assertFalse(self.ledger.status(DAY_AFTER)["account"]["prior_session_exposure"])


if __name__ == "__main__":
    unittest.main()
