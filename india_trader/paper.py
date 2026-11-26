"""Local paper-order ledger. All fills are simulated from completed candles."""

from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import sqlite3
from typing import Iterable

from .models import Bar, Instrument, Session
from .risk import (
    AccountRisk, BAR_INTERVAL, Costs, RiskBlocked, RiskConfig, Ticket, TradePlan,
    entry_bar_start, local, size_ticket,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS previews (
    digest TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    digest TEXT PRIMARY KEY REFERENCES previews(digest),
    symbol TEXT NOT NULL,
    day TEXT NOT NULL,
    signal_at TEXT NOT NULL,
    setup TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    next_bar TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('PENDING', 'OPEN', 'CLOSED', 'CANCELED')),
    entry_at TEXT,
    entry_price REAL,
    exit_at TEXT,
    exit_price REAL,
    net_pnl REAL,
    fees REAL,
    exit_reason TEXT,
    note TEXT,
    exit_pending INTEGER NOT NULL DEFAULT 0,
    UNIQUE(day, symbol, signal_at, setup)
);
"""


def _json(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        default=lambda stamp: stamp.isoformat(), allow_nan=False,
    )


def _payload(row: sqlite3.Row) -> dict:
    return json.loads(row["payload"])


def _timestamp(value: str) -> datetime:
    return local(datetime.fromisoformat(value))


def _ticket(row: sqlite3.Row) -> dict:
    return _payload(row)["ticket"]


class PaperLedger:
    """SQLite serializes approvals and position updates across CLI processes."""

    def __init__(self, db_path: str | Path, config: RiskConfig):
        self.db_path = Path(db_path)
        self.config = config
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(_SCHEMA)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO settings VALUES ('initial_cash', ?)",
                (_json(config.initial_cash),),
            )
            initial_cash = connection.execute(
                "SELECT value FROM settings WHERE key = 'initial_cash'"
            ).fetchone()[0]
            if float(initial_cash) != config.initial_cash:
                raise RiskBlocked("LEDGER_INITIAL_CASH_CHANGED", "use the original cash amount or a fresh paper ledger")

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _account(self, connection: sqlite3.Connection, day: date) -> AccountRisk:
        orders = connection.execute(
            "SELECT orders.*, previews.payload FROM orders JOIN previews USING (digest)"
        ).fetchall()
        closed = [row for row in orders if row["state"] == "CLOSED"]
        active = [row for row in orders if row["state"] in ("PENDING", "OPEN")]
        day_text = day.isoformat()
        equity = round(self.config.initial_cash + sum(row["net_pnl"] for row in closed), 2)
        return AccountRisk(
            equity=equity,
            available_cash=round(equity - sum(_ticket(row)["maximum_cash"] for row in active), 2),
            realized_today=round(sum(row["net_pnl"] for row in closed if row["day"] == day_text), 2),
            committed_risk=round(sum(_ticket(row)["planned_loss"] for row in active), 2),
            trades_today=sum(row["day"] == day_text and row["state"] != "CANCELED" for row in orders),
            open_symbols=tuple(row["symbol"] for row in active),
            prior_session_exposure=any(row["day"] < day_text for row in active),
        )

    @staticmethod
    def _chronology(connection: sqlite3.Connection, now: datetime) -> None:
        if connection.execute(
            """
            SELECT 1 FROM orders WHERE approved_at > ? OR exit_at > ?
               OR (state IN ('OPEN', 'CLOSED') AND next_bar > ?) LIMIT 1
            """,
            (now.isoformat(), now.isoformat(), now.isoformat()),
        ).fetchone():
            raise RiskBlocked("LEDGER_NEWER_THAN_REQUESTED_TIME")

    def preview(
        self, plan: TradePlan, instrument: Instrument, session: Session, latest_bar: Bar, now: datetime,
    ) -> Ticket:
        now = local(now)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._chronology(connection, now)
            account = self._account(connection, session.day)
            ticket = size_ticket(plan, instrument, session, latest_bar, now, self.config, account)
            payload = _json({"ticket": asdict(ticket), "policy": asdict(self.config)})
            digest = sha256(payload.encode("utf-8")).hexdigest()
            connection.execute("INSERT OR IGNORE INTO previews VALUES (?, ?)", (digest, payload))
            return replace(ticket, digest=digest)

    def inspect_ticket(self, digest: str) -> dict:
        with self._connection() as connection:
            row = connection.execute("SELECT payload FROM previews WHERE digest = ?", (digest,)).fetchone()
            if row is None:
                raise RiskBlocked("UNKNOWN_TICKET")
            return {**_ticket(row), "digest": digest}

    def confirm(
        self, digest: str, instrument: Instrument, session: Session, latest_bar: Bar, now: datetime,
    ) -> dict:
        now = local(now)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute("SELECT * FROM orders WHERE digest = ?", (digest,)).fetchone()
            if prior is not None:
                return dict(prior)
            self._chronology(connection, now)
            preview = connection.execute("SELECT payload FROM previews WHERE digest = ?", (digest,)).fetchone()
            if preview is None:
                raise RiskBlocked("UNKNOWN_TICKET")
            payload = _payload(preview)
            stored = payload["ticket"]
            if not _timestamp(stored["as_of"]) <= now < _timestamp(stored["expires_at"]):
                raise RiskBlocked("TICKET_EXPIRED_OR_NOT_YET_VALID")
            if payload["policy"] != asdict(self.config):
                raise RiskBlocked("RISK_POLICY_CHANGED")
            plan_fields = stored["plan"].copy()
            plan_fields["signal_at"] = _timestamp(plan_fields["signal_at"])
            plan = TradePlan(**plan_fields)
            if connection.execute(
                "SELECT 1 FROM orders WHERE day = ? AND symbol = ? AND signal_at = ? AND setup = ?",
                (session.day.isoformat(), plan.symbol, plan.signal_at.isoformat(), plan.setup),
            ).fetchone():
                raise RiskBlocked("SIGNAL_ALREADY_CONFIRMED")
            account = self._account(connection, session.day)
            current = size_ticket(plan, instrument, session, latest_bar, now, self.config, account)
            terms = (
                "session_close", "latest_entry_at", "flatten_at", "tick_size", "lower_band", "upper_band",
                "instrument_source", "instrument_isin", "maximum_entry", "stop", "target",
            )
            current_values = json.loads(_json(asdict(current)))
            if any(current_values[name] != stored[name] for name in terms):
                raise RiskBlocked("TICKET_TERMS_CHANGED")
            if current.instrument_asof < _timestamp(stored["instrument_asof"]):
                raise RiskBlocked("INSTRUMENT_EVIDENCE_REGRESSED")
            if current.observed_bar_at < _timestamp(stored["observed_bar_at"]):
                raise RiskBlocked("CANDLE_EVIDENCE_REGRESSED")
            if current.observed_bar_at == _timestamp(stored["observed_bar_at"]) and current.observed_close != stored["observed_close"]:
                raise RiskBlocked("TICKET_CANDLE_REVISED")
            if current.quantity < stored["quantity"]:
                raise RiskBlocked("TICKET_SIZE_NO_LONGER_ALLOWED")
            connection.execute(
                """
                INSERT INTO orders (digest, symbol, day, signal_at, setup, approved_at, next_bar, state)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING')
                """,
                (
                    digest, plan.symbol, session.day.isoformat(), plan.signal_at.isoformat(), plan.setup,
                    now.isoformat(), entry_bar_start(now, session).isoformat(),
                ),
            )
            return dict(connection.execute("SELECT * FROM orders WHERE digest = ?", (digest,)).fetchone())

    def advance(self, symbol: str, bars: Iterable[Bar], now: datetime) -> list[dict]:
        """Progress confirmed entries and exits with ordered, contiguous completed bars."""
        now = local(now)
        completed: dict[datetime, Bar] = {}
        for bar in bars:
            start = local(bar.ts)
            if bar.symbol != symbol or start + BAR_INTERVAL > now:
                continue
            if start in completed:
                raise RiskBlocked("DUPLICATE_PAPER_BAR", start.isoformat())
            prices = (bar.open, bar.high, bar.low, bar.close)
            if (
                not all(isfinite(price) and price > 0 for price in prices)
                or not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high
                or bar.volume < 0
            ):
                raise RiskBlocked("INVALID_PAPER_BAR", start.isoformat())
            completed[start] = bar

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            orders = connection.execute(
                """
                SELECT orders.*, previews.payload FROM orders JOIN previews USING (digest)
                WHERE symbol = ? AND state IN ('PENDING', 'OPEN') AND approved_at <= ?
                ORDER BY approved_at
                """,
                (symbol, now.isoformat()),
            ).fetchall()
            changed = []
            for row in orders:
                order = self._advance_order(dict(row), completed, now)
                if order is not None:
                    self._write_order(connection, order)
                    changed.append(order)
            return changed

    def cancel(self, digest: str, now: datetime) -> dict:
        """Cancel a pending paper entry and retain the approval and cancellation history."""
        now = local(now)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM orders WHERE digest = ?", (digest,)).fetchone()
            if row is None:
                raise RiskBlocked("UNKNOWN_CONFIRMED_TICKET")
            order = dict(row)
            if order["state"] == "CANCELED":
                return order
            if order["state"] != "PENDING":
                raise RiskBlocked("CANNOT_CANCEL_PAPER_FILL")
            if now < _timestamp(order["approved_at"]):
                raise RiskBlocked("CANCELLATION_BEFORE_APPROVAL")
            self._chronology(connection, now)
            order.update(
                state="CANCELED", exit_at=now.isoformat(), exit_reason="MANUAL_PAPER_CANCEL",
                note=("AFTER_MODELED_ENTRY_WITHOUT_A_RECORDED_FILL"
                      if now >= _timestamp(order["next_bar"]) else None),
            )
            self._write_order(connection, order)
            return order

    @staticmethod
    def _advance_order(order: dict, bars: dict[datetime, Bar], now: datetime) -> dict | None:
        payload = _payload(order)
        del order["payload"]
        ticket = payload["ticket"]
        costs = Costs(**payload["policy"]["costs"])
        flatten = _timestamp(ticket["flatten_at"])
        tick, lower, upper = (ticket[key] for key in ("tick_size", "lower_band", "upper_band"))
        next_bar = _timestamp(order["next_bar"])
        original = order.copy()
        while next_bar < flatten and next_bar + BAR_INTERVAL <= now:
            bar = bars.get(next_bar)
            if bar is None:
                order["note"] = f"MISSING_COMPLETED_BAR:{next_bar.isoformat()}"
                break
            if bar.low < lower or bar.high > upper:
                order["note"] = f"BAR_OUTSIDE_VERIFIED_BAND:{next_bar.isoformat()}"
                break
            bar_end = next_bar + BAR_INTERVAL
            order["next_bar"] = bar_end.isoformat()
            order["note"] = None
            if order["state"] == "PENDING":
                fill = costs.buy_price(bar.open, tick)
                if (
                    bar.volume == 0 or fill >= upper or fill <= lower
                    or bar.open <= ticket["stop"] or fill > ticket["maximum_entry"]
                ):
                    order.update(state="CANCELED", exit_at=bar_end.isoformat(), exit_reason="NEXT_BAR_NOT_EXECUTABLE")
                    break
                order.update(state="OPEN", entry_at=next_bar.isoformat(), entry_price=fill)

            timed = bar_end == flatten
            locked_sell = bar.volume == 0 or bar.high <= lower
            stop_hit = bar.low <= ticket["stop"]
            if locked_sell and (timed or stop_hit or order["exit_pending"]):
                # A locked cutoff leaves exposure for the operator to reconcile.
                # Later candles never fabricate an on-time flatten.
                order.update(exit_pending=1, note="FLATTEN_UNFILLED" if timed else "STOP_EXIT_UNFILLED")
            elif not locked_sell:
                stop_fill = min(bar.open, ticket["stop"])
                if order["exit_pending"]:
                    exit_at, raw_price, reason = next_bar, bar.open, "DEFERRED_STOP"
                elif stop_hit:
                    exit_at, raw_price, reason = bar_end, stop_fill, "STOP"
                elif bar.high >= ticket["target"]:
                    exit_at, raw_price, reason = bar_end, ticket["target"], "TARGET"
                elif timed:
                    exit_at, raw_price, reason = bar_end, bar.close, "TIME_EXIT"
                else:
                    next_bar = bar_end
                    continue
                sell = min(upper, max(lower, costs.sell_price(raw_price, tick)))
                _close(order, ticket, costs, sell, exit_at, reason)
                break
            next_bar = bar_end
        return order if order != original else None

    @staticmethod
    def _write_order(connection: sqlite3.Connection, order: dict) -> None:
        connection.execute(
            """
            UPDATE orders SET next_bar = :next_bar, state = :state, entry_at = :entry_at,
                entry_price = :entry_price, exit_at = :exit_at, exit_price = :exit_price,
                net_pnl = :net_pnl, fees = :fees, exit_reason = :exit_reason,
                note = :note, exit_pending = :exit_pending
            WHERE digest = :digest
            """,
            order,
        )

    def reconcile(self, digest: str, exit_price: float, observed_at: datetime, note: str) -> dict:
        """Record a manually observed paper exit when candle data could not resolve it."""
        observed_at = local(observed_at)
        if not note.strip() or not isfinite(exit_price) or exit_price <= 0:
            raise RiskBlocked("RECONCILIATION_REQUIRES_PRICE_AND_EVIDENCE")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT orders.*, previews.payload FROM orders JOIN previews USING (digest)
                WHERE digest = ? AND state = 'OPEN'
                """,
                (digest,),
            ).fetchone()
            if row is None:
                raise RiskBlocked("NO_OPEN_PAPER_POSITION")
            order = dict(row)
            payload = _payload(order)
            del order["payload"]
            ticket = payload["ticket"]
            if (
                not _timestamp(order["entry_at"]) < observed_at <= _timestamp(ticket["session_close"])
                or not ticket["lower_band"] <= exit_price <= ticket["upper_band"]
            ):
                raise RiskBlocked("RECONCILIATION_OUTSIDE_ENTRY_SESSION_OR_BAND")
            if Decimal(str(exit_price)) % Decimal(str(ticket["tick_size"])):
                raise RiskBlocked("RECONCILIATION_PRICE_OFF_TICK")
            costs = Costs(**payload["policy"]["costs"])
            _close(order, ticket, costs, exit_price, observed_at, "MANUAL_PAPER_RECONCILIATION")
            order["note"] = note.strip()
            self._write_order(connection, order)
            return order

    def status(self, day: date) -> dict:
        with self._connection() as connection:
            connection.execute("BEGIN")
            orders = [
                dict(row) for row in connection.execute(
                    """
                    SELECT orders.*, previews.payload FROM orders JOIN previews USING (digest)
                    WHERE day = ? OR substr(exit_at, 1, 10) = ? OR state IN ('PENDING', 'OPEN')
                    ORDER BY approved_at
                    """,
                    (day.isoformat(), day.isoformat()),
                )
            ]
            for order in orders:
                ticket = _ticket(order)
                del order["payload"]
                order["quantity"] = ticket["quantity"]
                order["maximum_entry"] = ticket["maximum_entry"]
                order["stop"] = ticket["stop"]
                order["target"] = ticket["target"]
                order["flatten_at"] = ticket["flatten_at"]
                if order["day"] < day.isoformat() and order["state"] in ("PENDING", "OPEN"):
                    order["needs_review"] = "UNRESOLVED_PRIOR_SESSION"
            return {
                "mode": "paper", "day": day.isoformat(),
                "account": asdict(self._account(connection, day)), "orders": orders,
            }


def _close(
    order: dict, ticket: dict, costs: Costs, sell: float, exit_at: datetime, reason: str,
) -> None:
    quantity = ticket["quantity"]
    charges = costs.round_trip(order["entry_price"], sell, quantity)
    order.update(
        state="CLOSED", exit_price=sell, exit_at=exit_at.isoformat(), exit_reason=reason,
        net_pnl=round((sell - order["entry_price"]) * quantity - charges, 2), fees=charges,
        exit_pending=0, note=None,
    )
