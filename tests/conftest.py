from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional

import pytest

from accrual_agent.config import Settings
from accrual_agent.register.repository import Repository, SCHEMA
from accrual_agent.runtime import Runtime


@pytest.fixture
def settings(tmp_path) -> Settings:
    base = Settings(_env_file=None)
    return base.model_copy(update={
        "mode": "mock",
        "outbound_mode": "dry_run",
        "db_path": str(tmp_path / "test.db"),
        "output_dir": str(tmp_path / "output"),
        "artifacts_dir": str(tmp_path / "artifacts"),
        "team_lead_email": "lead@yourco.example",
        "escalation_channels": "email",
    })


def simulated_now(day: int, settings: Settings) -> dt.datetime:
    """9am company-time on the Nth business day after the 2026-06 period end."""
    from zoneinfo import ZoneInfo

    probe = Runtime(settings)
    run_date = probe.calendar.add_business_days(dt.date(2026, 6, 30), day)
    return dt.datetime.combine(
        run_date, dt.time(9, 0), tzinfo=ZoneInfo(settings.close_timezone)
    )


@pytest.fixture
def runtime_factory(settings):
    def build(day: int = 1) -> Runtime:
        return Runtime(settings, now_provider=lambda: simulated_now(day, settings))

    return build


# Database export/import testing utilities


@pytest.fixture
def temp_db_dir():
    """Temporary directory for test databases."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def blank_test_db(temp_db_dir: Path) -> Path:
    """Create a blank test database with schema."""
    db_path = temp_db_dir / "test.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for statement in SCHEMA.split(";"):
        if statement.strip():
            cursor.execute(statement)

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def test_db_with_snapshot(temp_db_dir: Path) -> Path:
    """Create an isolated test database (blank by default).

    Can be called with a snapshot_path parameter to load from existing DB.
    """
    db_path = temp_db_dir / "test_accruals.db"

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for statement in SCHEMA.split(";"):
        if statement.strip():
            cursor.execute(statement)

    conn.commit()
    conn.close()

    return db_path


@pytest.fixture
def test_repository(blank_test_db: Path) -> Repository:
    """Create a Repository instance backed by an isolated test database."""
    return Repository(blank_test_db)


class IsolatedDatabaseContext:
    """Context manager for working with isolated test databases."""

    def __init__(self, snapshot_path: Optional[Path] = None):
        self.snapshot_path = snapshot_path
        self.tmpdir: Optional[TemporaryDirectory] = None
        self.db_path: Optional[Path] = None

    def __enter__(self) -> Path:
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"

        if self.snapshot_path and self.snapshot_path.exists():
            shutil.copy2(self.snapshot_path, self.db_path)
        else:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    cursor.execute(statement)
            conn.commit()
            conn.close()

        return self.db_path

    def __exit__(self, *args):
        if self.tmpdir:
            self.tmpdir.cleanup()


def make_test_settings(db_path: Path) -> Settings:
    """Create a Settings object configured for a test database."""
    base = Settings(_env_file=None)
    return base.model_copy(
        update={
            "mode": "mock",
            "outbound_mode": "dry_run",
            "db_path": str(db_path),
            "output_dir": str(db_path.parent / "output"),
            "artifacts_dir": str(db_path.parent / "artifacts"),
        }
    )
