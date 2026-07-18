"""SQLite persistence for the accrual register.

One mutable head row per accrual line; every amount/status change appends an
audit row (old -> new, source, timestamp, actor). WAL mode for concurrent
reads; write serialization is handled by the process-level advisory lock.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from ..models import (
    AccrualLine,
    AccrualStatus,
    CommRecord,
    Escalation,
    EscalationReason,
    JournalEntry,
    SourceType,
    ThreadStatus,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS accrual_lines (
    line_id         TEXT PRIMARY KEY,
    natural_key     TEXT NOT NULL UNIQUE,     -- source_type|source_ref|period dedupe key
    vendor_id       TEXT NOT NULL,
    vendor_name     TEXT NOT NULL,
    period          TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    source_ref      TEXT NOT NULL,
    estimate_basis  TEXT NOT NULL DEFAULT '',
    amount          TEXT NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'USD',
    exchange_rate   TEXT NOT NULL DEFAULT '1',
    base_amount     TEXT NOT NULL DEFAULT '0',
    gl_account      TEXT,
    cost_center     TEXT,
    subsidiary_id   TEXT,
    status          TEXT NOT NULL,
    provisional     INTEGER NOT NULL DEFAULT 0,
    comm_suppressed INTEGER NOT NULL DEFAULT 0,
    ref_token       TEXT NOT NULL DEFAULT '',
    thread_status   TEXT NOT NULL DEFAULT 'not_started',
    close_risk      INTEGER NOT NULL DEFAULT 0,
    confirmed_amount TEXT,
    confirmed_source TEXT,
    invoice_number  TEXT,
    invoice_eta     TEXT,
    hold_reason     TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id     TEXT,
    ts          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    source      TEXT NOT NULL,
    field       TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT
);

CREATE TABLE IF NOT EXISTS comm_log (
    comm_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id     TEXT NOT NULL,
    direction   TEXT NOT NULL,
    stage       TEXT NOT NULL,
    recipient   TEXT,
    sender      TEXT,
    subject     TEXT NOT NULL,
    message_id  TEXT,
    in_reply_to TEXT,
    body_preview TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '[]',
    sent_at     TEXT,
    delivery    TEXT NOT NULL DEFAULT 'logged'
);

CREATE TABLE IF NOT EXISTS journal_entries (
    je_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id      TEXT NOT NULL,
    external_id  TEXT NOT NULL UNIQUE,
    tran_date    TEXT NOT NULL,
    reversal_date TEXT NOT NULL,
    subsidiary_id TEXT NOT NULL,
    debit_account TEXT NOT NULL,
    credit_account TEXT NOT NULL,
    amount       TEXT NOT NULL,
    currency     TEXT NOT NULL,
    exchange_rate TEXT NOT NULL,
    memo         TEXT NOT NULL,
    estimate_based INTEGER NOT NULL DEFAULT 0,
    netsuite_id  TEXT,
    posted_at    TEXT
);

CREATE TABLE IF NOT EXISTS escalations (
    escalation_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id        TEXT,
    reason         TEXT NOT NULL,
    severity       TEXT NOT NULL DEFAULT 'medium',
    detail         TEXT NOT NULL DEFAULT '',
    suggested_action TEXT NOT NULL DEFAULT '',
    raised_at      TEXT NOT NULL,
    last_raised_at TEXT NOT NULL,
    raise_count    INTEGER NOT NULL DEFAULT 1,
    resolved_at    TEXT,
    channels       TEXT NOT NULL DEFAULT '[]',
    UNIQUE (line_id, reason)
);

CREATE TABLE IF NOT EXISTS processed_messages (
    message_id  TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS line_sequence (
    period TEXT PRIMARY KEY,
    next_seq INTEGER NOT NULL
);
"""


class Repository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ── accrual lines ────────────────────────────────────────────────────

    @staticmethod
    def natural_key(source_type: SourceType | str, source_ref: str, period: str) -> str:
        return f"{source_type}|{source_ref}|{period}"

    def next_line_id(self, period: str) -> str:
        cur = self.conn.execute(
            "INSERT INTO line_sequence (period, next_seq) VALUES (?, 2) "
            "ON CONFLICT(period) DO UPDATE SET next_seq = next_seq + 1 "
            "RETURNING next_seq - 1"
        , (period,))
        seq = cur.fetchone()[0]
        return f"ACR-{period}-{seq:04d}"

    def get_line(self, line_id: str) -> AccrualLine | None:
        row = self.conn.execute(
            "SELECT * FROM accrual_lines WHERE line_id = ?", (line_id,)
        ).fetchone()
        return _row_to_line(row) if row else None

    def get_line_by_natural_key(self, key: str) -> AccrualLine | None:
        row = self.conn.execute(
            "SELECT * FROM accrual_lines WHERE natural_key = ?", (key,)
        ).fetchone()
        return _row_to_line(row) if row else None

    def get_line_by_token(self, ref_token: str) -> AccrualLine | None:
        row = self.conn.execute(
            "SELECT * FROM accrual_lines WHERE ref_token = ?", (ref_token,)
        ).fetchone()
        return _row_to_line(row) if row else None

    def insert_line(self, line: AccrualLine) -> None:
        now = _utcnow()
        self.conn.execute(
            """INSERT INTO accrual_lines (
                line_id, natural_key, vendor_id, vendor_name, period, source_type,
                source_ref, estimate_basis, amount, currency, exchange_rate,
                base_amount, gl_account, cost_center, subsidiary_id, status,
                provisional, comm_suppressed, ref_token, thread_status, close_risk,
                confirmed_amount, confirmed_source, invoice_number, invoice_eta,
                hold_reason, notes, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                line.line_id,
                self.natural_key(line.source_type, line.source_ref, line.period),
                line.vendor_id, line.vendor_name, line.period, line.source_type.value,
                line.source_ref, line.estimate_basis, str(line.amount), line.currency,
                str(line.exchange_rate), str(line.base_amount), line.gl_account,
                line.cost_center, line.subsidiary_id, line.status.value,
                int(line.provisional), int(line.comm_suppressed), line.ref_token,
                line.thread_status.value, int(line.close_risk),
                _opt_str(line.confirmed_amount), line.confirmed_source,
                line.invoice_number, _opt_iso(line.invoice_eta), line.hold_reason,
                line.notes, now, now,
            ),
        )
        self.conn.commit()

    def update_line_fields(
        self, line_id: str, actor: str, source: str, **fields: object
    ) -> None:
        """Update head-row fields, audit-logging every actual change."""
        current = self.conn.execute(
            "SELECT * FROM accrual_lines WHERE line_id = ?", (line_id,)
        ).fetchone()
        if current is None:
            raise KeyError(f"unknown accrual line {line_id}")
        now = _utcnow()
        sets, params, audits = [], [], []
        for field_name, new_value in fields.items():
            stored = _to_stored(new_value)
            old = current[field_name]
            if old == stored:
                continue
            sets.append(f"{field_name} = ?")
            params.append(stored)
            audits.append((line_id, now, actor, source, field_name, _text(old), _text(stored)))
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(now)
        params.append(line_id)
        self.conn.execute(
            f"UPDATE accrual_lines SET {', '.join(sets)} WHERE line_id = ?", params
        )
        self.conn.executemany(
            "INSERT INTO audit_log (line_id, ts, actor, source, field, old_value, new_value) "
            "VALUES (?,?,?,?,?,?,?)",
            audits,
        )
        self.conn.commit()

    def lines(
        self,
        period: str | None = None,
        statuses: list[AccrualStatus] | None = None,
    ) -> list[AccrualLine]:
        query = "SELECT * FROM accrual_lines WHERE 1=1"
        params: list[object] = []
        if period:
            query += " AND period = ?"
            params.append(period)
        if statuses:
            query += f" AND status IN ({','.join('?' * len(statuses))})"
            params.extend(s.value for s in statuses)
        query += " ORDER BY line_id"
        return [_row_to_line(r) for r in self.conn.execute(query, params)]

    # ── audit ────────────────────────────────────────────────────────────

    def audit_rows(self, line_id: str | None = None, limit: int = 200) -> list[sqlite3.Row]:
        if line_id:
            return list(self.conn.execute(
                "SELECT * FROM audit_log WHERE line_id = ? ORDER BY id DESC LIMIT ?",
                (line_id, limit),
            ))
        return list(self.conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ))

    def add_audit(
        self, line_id: str | None, actor: str, source: str, field: str,
        old_value: str | None, new_value: str | None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (line_id, ts, actor, source, field, old_value, new_value) "
            "VALUES (?,?,?,?,?,?,?)",
            (line_id, _utcnow(), actor, source, field, old_value, new_value),
        )
        self.conn.commit()

    # ── comm log ─────────────────────────────────────────────────────────

    def add_comm(self, rec: CommRecord) -> int:
        cur = self.conn.execute(
            """INSERT INTO comm_log (line_id, direction, stage, recipient, sender,
                subject, message_id, in_reply_to, body_preview, attachments, sent_at, delivery)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec.line_id, rec.direction, rec.stage, rec.recipient, rec.sender,
                rec.subject, rec.message_id, rec.in_reply_to, rec.body_preview,
                json.dumps(rec.attachment_paths),
                _opt_iso(rec.sent_at) or _utcnow(), rec.delivery,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def comms_for_line(self, line_id: str) -> list[CommRecord]:
        rows = self.conn.execute(
            "SELECT * FROM comm_log WHERE line_id = ? ORDER BY comm_id", (line_id,)
        )
        return [_row_to_comm(r) for r in rows]

    def sent_stages(self, line_id: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT stage FROM comm_log WHERE line_id = ? AND direction = 'outbound'",
            (line_id,),
        )
        return {r["stage"] for r in rows}

    def outbound_message_ids(self) -> dict[str, str]:
        """message_id -> line_id for In-Reply-To matching."""
        rows = self.conn.execute(
            "SELECT message_id, line_id FROM comm_log "
            "WHERE direction = 'outbound' AND message_id IS NOT NULL"
        )
        return {r["message_id"]: r["line_id"] for r in rows}

    def initial_sent_at(self, line_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT sent_at FROM comm_log WHERE line_id = ? AND stage = 'initial' "
            "AND direction = 'outbound' ORDER BY comm_id LIMIT 1",
            (line_id,),
        ).fetchone()
        return row["sent_at"] if row else None

    # ── journal entries ──────────────────────────────────────────────────

    def add_journal_entry(self, je: JournalEntry) -> int:
        cur = self.conn.execute(
            """INSERT INTO journal_entries (line_id, external_id, tran_date, reversal_date,
                subsidiary_id, debit_account, credit_account, amount, currency,
                exchange_rate, memo, estimate_based, netsuite_id, posted_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                je.line_id, je.external_id, je.tran_date.isoformat(),
                je.reversal_date.isoformat(), je.subsidiary_id, je.debit_account,
                je.credit_account, str(je.amount), je.currency, str(je.exchange_rate),
                je.memo, int(je.estimate_based), je.netsuite_id,
                _opt_iso(je.posted_at) or _utcnow(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def je_for_line(self, line_id: str) -> JournalEntry | None:
        row = self.conn.execute(
            "SELECT * FROM journal_entries WHERE line_id = ? ORDER BY je_id DESC LIMIT 1",
            (line_id,),
        ).fetchone()
        return _row_to_je(row) if row else None

    def je_by_external_id(self, external_id: str) -> JournalEntry | None:
        row = self.conn.execute(
            "SELECT * FROM journal_entries WHERE external_id = ?", (external_id,)
        ).fetchone()
        return _row_to_je(row) if row else None

    def journal_entries(self, period: str | None = None) -> list[JournalEntry]:
        if period:
            rows = self.conn.execute(
                "SELECT je.* FROM journal_entries je JOIN accrual_lines l "
                "ON l.line_id = je.line_id WHERE l.period = ? ORDER BY je.je_id",
                (period,),
            )
        else:
            rows = self.conn.execute("SELECT * FROM journal_entries ORDER BY je_id")
        return [_row_to_je(r) for r in rows]

    # ── escalations ──────────────────────────────────────────────────────

    def get_escalation(self, line_id: str | None, reason: EscalationReason) -> Escalation | None:
        row = self.conn.execute(
            "SELECT * FROM escalations WHERE line_id IS ? AND reason = ?",
            (line_id, reason.value),
        ).fetchone()
        return _row_to_escalation(row) if row else None

    def upsert_escalation(self, esc: Escalation) -> Escalation:
        now = _utcnow()
        existing = self.get_escalation(esc.line_id, esc.reason)
        if existing is None:
            self.conn.execute(
                """INSERT INTO escalations (line_id, reason, severity, detail,
                    suggested_action, raised_at, last_raised_at, raise_count, channels)
                   VALUES (?,?,?,?,?,?,?,1,?)""",
                (
                    esc.line_id, esc.reason.value, esc.severity, esc.detail,
                    esc.suggested_action, now, now, json.dumps(esc.channels),
                ),
            )
        else:
            self.conn.execute(
                """UPDATE escalations SET severity = ?, detail = ?, suggested_action = ?,
                    last_raised_at = ?, raise_count = raise_count + 1, channels = ?,
                    resolved_at = NULL
                   WHERE line_id IS ? AND reason = ?""",
                (
                    esc.severity, esc.detail, esc.suggested_action, now,
                    json.dumps(esc.channels), esc.line_id, esc.reason.value,
                ),
            )
        self.conn.commit()
        result = self.get_escalation(esc.line_id, esc.reason)
        assert result is not None
        return result

    def touch_escalation(self, line_id: str | None, reason: EscalationReason) -> None:
        self.conn.execute(
            "UPDATE escalations SET last_raised_at = ?, raise_count = raise_count + 1 "
            "WHERE line_id IS ? AND reason = ?",
            (_utcnow(), line_id, reason.value),
        )
        self.conn.commit()

    def resolve_escalation(self, line_id: str | None, reason: EscalationReason) -> None:
        self.conn.execute(
            "UPDATE escalations SET resolved_at = ? WHERE line_id IS ? AND reason = ? "
            "AND resolved_at IS NULL",
            (_utcnow(), line_id, reason.value),
        )
        self.conn.commit()

    def open_escalations(self) -> list[Escalation]:
        rows = self.conn.execute(
            "SELECT * FROM escalations WHERE resolved_at IS NULL ORDER BY escalation_id"
        )
        return [_row_to_escalation(r) for r in rows]

    # ── inbound dedupe ───────────────────────────────────────────────────

    def message_processed(self, message_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM processed_messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None

    def mark_message_processed(self, message_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO processed_messages (message_id, processed_at) VALUES (?,?)",
            (message_id, _utcnow()),
        )
        self.conn.commit()


# ── row mappers / helpers ────────────────────────────────────────────────────


def _utcnow() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def _opt_iso(value: dt.date | dt.datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _to_stored(value: object) -> object:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    if isinstance(value, AccrualStatus | SourceType | ThreadStatus | EscalationReason):
        return value.value
    return value


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _row_to_line(row: sqlite3.Row) -> AccrualLine:
    return AccrualLine(
        line_id=row["line_id"],
        vendor_id=row["vendor_id"],
        vendor_name=row["vendor_name"],
        period=row["period"],
        source_type=SourceType(row["source_type"]),
        source_ref=row["source_ref"],
        estimate_basis=row["estimate_basis"],
        amount=Decimal(row["amount"]),
        currency=row["currency"],
        exchange_rate=Decimal(row["exchange_rate"]),
        base_amount=Decimal(row["base_amount"]),
        gl_account=row["gl_account"],
        cost_center=row["cost_center"],
        subsidiary_id=row["subsidiary_id"],
        status=AccrualStatus(row["status"]),
        provisional=bool(row["provisional"]),
        comm_suppressed=bool(row["comm_suppressed"]),
        ref_token=row["ref_token"],
        thread_status=ThreadStatus(row["thread_status"]),
        close_risk=bool(row["close_risk"]),
        confirmed_amount=Decimal(row["confirmed_amount"]) if row["confirmed_amount"] else None,
        confirmed_source=row["confirmed_source"],
        invoice_number=row["invoice_number"],
        invoice_eta=dt.date.fromisoformat(row["invoice_eta"]) if row["invoice_eta"] else None,
        hold_reason=row["hold_reason"],
        notes=row["notes"],
        created_at=dt.datetime.fromisoformat(row["created_at"]),
        updated_at=dt.datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_comm(row: sqlite3.Row) -> CommRecord:
    return CommRecord(
        comm_id=row["comm_id"],
        line_id=row["line_id"],
        direction=row["direction"],
        stage=row["stage"],
        recipient=row["recipient"],
        sender=row["sender"],
        subject=row["subject"],
        message_id=row["message_id"],
        in_reply_to=row["in_reply_to"],
        body_preview=row["body_preview"],
        attachment_paths=json.loads(row["attachments"]),
        sent_at=dt.datetime.fromisoformat(row["sent_at"]) if row["sent_at"] else None,
        delivery=row["delivery"],
    )


def _row_to_je(row: sqlite3.Row) -> JournalEntry:
    return JournalEntry(
        je_id=row["je_id"],
        line_id=row["line_id"],
        external_id=row["external_id"],
        tran_date=dt.date.fromisoformat(row["tran_date"]),
        reversal_date=dt.date.fromisoformat(row["reversal_date"]),
        subsidiary_id=row["subsidiary_id"],
        debit_account=row["debit_account"],
        credit_account=row["credit_account"],
        amount=Decimal(row["amount"]),
        currency=row["currency"],
        exchange_rate=Decimal(row["exchange_rate"]),
        memo=row["memo"],
        estimate_based=bool(row["estimate_based"]),
        netsuite_id=row["netsuite_id"],
        posted_at=dt.datetime.fromisoformat(row["posted_at"]) if row["posted_at"] else None,
    )


def _row_to_escalation(row: sqlite3.Row) -> Escalation:
    return Escalation(
        escalation_id=row["escalation_id"],
        line_id=row["line_id"],
        reason=EscalationReason(row["reason"]),
        severity=row["severity"],
        detail=row["detail"],
        suggested_action=row["suggested_action"],
        raised_at=dt.datetime.fromisoformat(row["raised_at"]),
        last_raised_at=dt.datetime.fromisoformat(row["last_raised_at"]),
        raise_count=row["raise_count"],
        resolved_at=dt.datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
        channels=json.loads(row["channels"]),
    )
