"""Export and import utilities for standalone accounting data databases.

Enables extracting the register into a standalone SQLite file suitable for
testing, development, or offline analysis. Preserves all accrual lines, audit
trails, communication logs, and journal entries.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

from ..config import Settings
from .repository import Repository

logger = logging.getLogger(__name__)


def export_database(
    settings: Settings,
    output_path: Path,
    include_audit: bool = True,
    include_comms: bool = True,
    period: str | None = None,
) -> Path:
    """Export accounting data to a standalone SQLite database.

    Args:
        settings: Runtime settings with db_path configured
        output_path: Where to write the standalone .db file
        include_audit: Whether to include audit trail logs
        include_comms: Whether to include communication logs
        period: Optional period filter (e.g., '2026-06'); None exports all

    Returns:
        Path to the created database file
    """
    src_db = Path(settings.db_path)
    if not src_db.exists():
        raise FileNotFoundError(f"Source database not found: {src_db}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Copy the entire database
    shutil.copy2(src_db, output_path)

    # If filtering by period, clean up irrelevant data
    if period:
        _filter_database_by_period(output_path, period, include_audit, include_comms)
    elif not include_audit or not include_comms:
        _clean_unused_tables(output_path, include_audit, include_comms)

    return output_path.resolve()


def import_database(
    source_path: Path,
    settings: Settings,
    *,
    backup: bool = True,
) -> Path:
    """Import a standalone database into the runtime.

    Useful for restoring test data or loading an exported snapshot. This
    overwrites the runtime database, so by default the existing file (if any)
    is preserved alongside it as a `.bak` copy.

    Args:
        source_path: Path to the standalone .db file
        settings: Runtime settings where to import to
        backup: If the destination already exists, copy it to `<dest>.bak`
            before overwriting (default True)

    Returns:
        Path to the destination database
    """
    if not source_path.exists():
        raise FileNotFoundError(f"Source database not found: {source_path}")

    dest_db = Path(settings.db_path)
    dest_db.parent.mkdir(parents=True, exist_ok=True)

    if backup and dest_db.exists():
        backup_path = dest_db.with_suffix(dest_db.suffix + ".bak")
        shutil.copy2(dest_db, backup_path)
        logger.info("Backed up existing runtime database to %s", backup_path)

    shutil.copy2(source_path, dest_db)
    return dest_db.resolve()


def list_database_snapshots(snapshots_dir: Path) -> list[tuple[str, Path, dict]]:
    """List available standalone database snapshots with metadata.

    Returns list of (name, path, metadata) tuples where metadata includes
    line counts and period coverage.
    """
    if not snapshots_dir.exists():
        return []

    snapshots = []
    for db_file in snapshots_dir.glob("*.db"):
        try:
            conn = sqlite3.connect(db_file)
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM accrual_lines")
            line_count = cursor.fetchone()[0]

            cursor.execute("SELECT DISTINCT period FROM accrual_lines ORDER BY period")
            periods = [row[0] for row in cursor.fetchall()]

            cursor.execute("SELECT COUNT(*) FROM journal_entries")
            je_count = cursor.fetchone()[0]

            conn.close()

            metadata = {
                "lines": line_count,
                "journal_entries": je_count,
                "periods": periods,
            }
            snapshots.append((db_file.stem, db_file, metadata))
        except sqlite3.Error as exc:
            logger.warning("Skipping unreadable snapshot %s: %s", db_file, exc)

    return sorted(snapshots, key=lambda x: x[1].stat().st_mtime, reverse=True)


def _filter_database_by_period(
    db_path: Path,
    period: str,
    include_audit: bool,
    include_comms: bool,
) -> None:
    """Filter database to a single period, optionally removing audit/comms."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Delete lines from other periods
    cursor.execute("DELETE FROM accrual_lines WHERE period != ?", (period,))

    # Cascade delete related records
    cursor.execute(
        """
        DELETE FROM journal_entries
        WHERE line_id NOT IN (SELECT line_id FROM accrual_lines)
        """
    )

    if include_audit:
        # Drop audit rows orphaned by the period filter (their line no longer exists).
        cursor.execute(
            """
            DELETE FROM audit_log
            WHERE line_id NOT IN (SELECT line_id FROM accrual_lines)
            """
        )
    else:
        cursor.execute("DELETE FROM audit_log")

    if include_comms:
        cursor.execute(
            """
            DELETE FROM comm_log
            WHERE line_id NOT IN (SELECT line_id FROM accrual_lines)
            """
        )
    else:
        cursor.execute("DELETE FROM comm_log")

    conn.commit()
    cursor.execute("VACUUM")
    conn.close()


def _clean_unused_tables(
    db_path: Path,
    include_audit: bool,
    include_comms: bool,
) -> None:
    """Remove audit and/or communication tables if not needed."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    if not include_audit:
        try:
            cursor.execute("DELETE FROM audit_log")
        except sqlite3.OperationalError:
            pass

    if not include_comms:
        try:
            cursor.execute("DELETE FROM comm_log")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    cursor.execute("VACUUM")
    conn.close()


def create_test_database(
    db_path: Path,
    snapshot_path: Path | None = None,
) -> Path:
    """Create an isolated test database.

    Args:
        db_path: Where to create the test database
        snapshot_path: Optional existing database to copy; if None, creates blank

    Returns:
        Path to the test database
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if snapshot_path and snapshot_path.exists():
        shutil.copy2(snapshot_path, db_path)
    else:
        # Repository() creates the schema (CREATE TABLE IF NOT EXISTS) and closes cleanly.
        Repository(db_path).conn.close()

    return db_path.resolve()
