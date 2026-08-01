"""Unit tests for accrual_agent.register.db_export."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from accrual_agent.models import CommRecord, JournalEntry, SourceType
from accrual_agent.register.db_export import (
    create_test_database,
    export_database,
    import_database,
    list_database_snapshots,
)
from accrual_agent.register.repository import Repository
from accrual_agent.register.service import RegisterService
from conftest import make_test_settings


def _seed_source_db(db_path: Path, *, bulk_audit_rows: int = 0) -> dict[str, str]:
    """Populate a source database with lines, JEs, audit and comm rows across two periods.

    Returns a mapping of period -> line_id for the seeded lines. Note that
    RegisterService.upsert_line already writes its own audit_log entry for
    line creation, so callers should not assume audit_log row counts are
    exactly one per explicit add_audit() call below.
    """
    repo = Repository(db_path)
    service = RegisterService(repo)
    line_ids: dict[str, str] = {}

    for period, ref in [("2026-05", "PO-1"), ("2026-06", "PO-2")]:
        line, _ = service.upsert_line(
            vendor_id="V-1",
            vendor_name="Acme Co",
            period=period,
            source_type=SourceType.NETSUITE_RECEIPT,
            source_ref=ref,
            estimate_basis="test",
            amount=Decimal("500.00"),
            currency="USD",
            exchange_rate=Decimal("1"),
            gl_account="6000",
            cost_center="CC-1",
            subsidiary_id="1",
        )
        line_ids[period] = line.line_id
        repo.add_journal_entry(
            JournalEntry(
                line_id=line.line_id,
                external_id=f"JE-{ref}",
                tran_date=date(2026, 6, 30),
                reversal_date=date(2026, 7, 1),
                subsidiary_id="1",
                debit_account="6000",
                credit_account="2100",
                amount=Decimal("500.00"),
                currency="USD",
                exchange_rate=Decimal("1"),
                memo="test je",
            )
        )
        repo.add_audit(line.line_id, "tester", "unit-test", "status", "old", "new")
        repo.add_comm(
            CommRecord(
                line_id=line.line_id,
                direction="outbound",
                stage="request",
                recipient="vendor@example.com",
                sender="ap@example.com",
                subject="Accrual confirmation",
            )
        )

    if bulk_audit_rows:
        # Pad the 2026-05 line with enough audit history that filtering it out
        # and VACUUMing actually frees whole pages (small dbs may not shrink
        # measurably from just a couple of rows).
        padding = "x" * 2000
        for i in range(bulk_audit_rows):
            repo.add_audit(
                line_ids["2026-05"], "tester", "unit-test", f"field-{i}", padding, padding
            )

    repo.close()
    return line_ids


@pytest.fixture
def source_settings(tmp_path):
    settings = make_test_settings(tmp_path / "source.db")
    _seed_source_db(Path(settings.db_path))
    return settings


@pytest.fixture
def source_settings_with_lines(tmp_path):
    settings = make_test_settings(tmp_path / "source.db")
    line_ids = _seed_source_db(Path(settings.db_path))
    return settings, line_ids


def _audit_and_comm_counts_for_line(db_path: Path, line_id: str) -> tuple[int, int]:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM audit_log WHERE line_id = ?", (line_id,))
        audit = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM comm_log WHERE line_id = ?", (line_id,))
        comm = cursor.fetchone()[0]
        return audit, comm
    finally:
        conn.close()


def _counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        result = {}
        for table in ("accrual_lines", "journal_entries", "audit_log", "comm_log"):
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            result[table] = cursor.fetchone()[0]
        return result
    finally:
        conn.close()


def test_export_database_full_copy(source_settings, tmp_path):
    source_counts = _counts(source_settings.db_path)
    out = export_database(source_settings, tmp_path / "export.db")
    assert _counts(out) == source_counts
    assert source_counts["accrual_lines"] == 2
    assert source_counts["journal_entries"] == 2
    assert source_counts["comm_log"] == 2


def test_export_database_missing_source_raises(tmp_path):
    settings = make_test_settings(tmp_path / "does-not-exist.db")
    with pytest.raises(FileNotFoundError):
        export_database(settings, tmp_path / "export.db")


def test_export_database_period_filter(source_settings_with_lines, tmp_path):
    settings, line_ids = source_settings_with_lines
    expected_audit, expected_comm = _audit_and_comm_counts_for_line(
        Path(settings.db_path), line_ids["2026-06"]
    )

    out = export_database(settings, tmp_path / "export.db", period="2026-06")
    counts = _counts(out)
    assert counts["accrual_lines"] == 1
    assert counts["journal_entries"] == 1
    assert counts["audit_log"] == expected_audit
    assert counts["comm_log"] == expected_comm


def test_export_database_excludes_audit_and_comms(source_settings, tmp_path):
    out = export_database(
        source_settings,
        tmp_path / "export.db",
        include_audit=False,
        include_comms=False,
        period="2026-06",
    )
    counts = _counts(out)
    assert counts["accrual_lines"] == 1
    assert counts["audit_log"] == 0
    assert counts["comm_log"] == 0


def test_export_database_filtering_shrinks_file_size(tmp_path):
    """VACUUM should reclaim space freed by filtering, not just leave holes in the file."""
    settings = make_test_settings(tmp_path / "source.db")
    _seed_source_db(Path(settings.db_path), bulk_audit_rows=200)

    unfiltered = export_database(settings, tmp_path / "unfiltered.db")
    filtered = export_database(
        settings,
        tmp_path / "filtered.db",
        include_audit=False,
        include_comms=False,
        period="2026-06",
    )
    assert filtered.stat().st_size < unfiltered.stat().st_size


def test_import_database_copies_content(source_settings, tmp_path):
    export_path = export_database(source_settings, tmp_path / "export.db")

    dest_settings = make_test_settings(tmp_path / "runtime" / "accruals.db")
    dest = import_database(export_path, dest_settings)

    assert _counts(dest) == _counts(export_path)


def test_import_database_backs_up_existing_destination(source_settings, tmp_path):
    export_path = export_database(source_settings, tmp_path / "export.db")

    dest_db = tmp_path / "runtime" / "accruals.db"
    dest_settings = make_test_settings(dest_db)
    create_test_database(dest_db)  # existing (blank) runtime db to be overwritten

    dest = import_database(export_path, dest_settings)

    backup = dest_db.with_suffix(dest_db.suffix + ".bak")
    assert backup.exists()
    assert _counts(backup) == {"accrual_lines": 0, "journal_entries": 0, "audit_log": 0, "comm_log": 0}
    assert _counts(dest) == _counts(export_path)


def test_import_database_missing_source_raises(tmp_path):
    dest_settings = make_test_settings(tmp_path / "runtime" / "accruals.db")
    with pytest.raises(FileNotFoundError):
        import_database(tmp_path / "missing.db", dest_settings)


def test_list_database_snapshots_returns_metadata(source_settings, tmp_path):
    snapshots_dir = tmp_path / "snapshots"
    export_database(source_settings, snapshots_dir / "full.db")
    export_database(source_settings, snapshots_dir / "june.db", period="2026-06")

    snapshots = list_database_snapshots(snapshots_dir)

    names = {name for name, _path, _meta in snapshots}
    assert names == {"full", "june"}
    metadata_by_name = {name: meta for name, _path, meta in snapshots}
    assert metadata_by_name["full"]["lines"] == 2
    assert metadata_by_name["june"]["lines"] == 1
    assert metadata_by_name["june"]["periods"] == ["2026-06"]


def test_list_database_snapshots_skips_unreadable_files(source_settings, tmp_path):
    snapshots_dir = tmp_path / "snapshots"
    export_database(source_settings, snapshots_dir / "good.db")
    (snapshots_dir / "not-a-database.db").write_text("not a sqlite file")

    snapshots = list_database_snapshots(snapshots_dir)

    assert [name for name, _path, _meta in snapshots] == ["good"]


def test_list_database_snapshots_empty_dir(tmp_path):
    assert list_database_snapshots(tmp_path / "does-not-exist") == []


def test_create_test_database_blank_has_schema(tmp_path):
    db_path = create_test_database(tmp_path / "blank.db")
    assert _counts(db_path) == {
        "accrual_lines": 0,
        "journal_entries": 0,
        "audit_log": 0,
        "comm_log": 0,
    }


def test_create_test_database_from_snapshot(source_settings, tmp_path):
    snapshot = export_database(source_settings, tmp_path / "snapshot.db")
    test_db = create_test_database(tmp_path / "test.db", snapshot_path=snapshot)
    assert _counts(test_db) == _counts(snapshot)
