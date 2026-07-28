"""Example test using the standalone database snapshot.

This demonstrates how to use the exported accounting data for testing.
"""

import shutil
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional

import pytest

from accrual_agent.config import Settings
from accrual_agent.models import AccrualStatus
from accrual_agent.register.repository import SCHEMA
from accrual_agent.runtime import Runtime


class IsolatedDatabaseContext:
    """Context manager for isolated test database (copy of conftest version)."""

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
    """Create Settings for a test database (copy of conftest version)."""
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


@pytest.fixture
def snapshot_path():
    """Path to the demo snapshot.

    To create this: uv run accrual-agent export-db --out snapshots/demo-2026-06.db
    """
    return Path("snapshots/demo-2026-06.db")


def test_demo_snapshot_exists(snapshot_path):
    """Verify the demo snapshot exists."""
    assert snapshot_path.exists(), (
        f"Demo snapshot not found at {snapshot_path}.\n"
        "Create it with: uv run accrual-agent export-db --out snapshots/demo-2026-06.db"
    )


def test_status_with_snapshot(snapshot_path):
    """Test that we can load and inspect the snapshot."""
    if not snapshot_path.exists():
        pytest.skip(f"Demo snapshot not found at {snapshot_path}")

    # Use the context manager for isolated test database
    with IsolatedDatabaseContext(snapshot_path=snapshot_path) as db_path:
        settings = make_test_settings(db_path)
        rt = Runtime(settings)

        # Now we have a Runtime with the demo data
        lines = rt.repo.lines(period="2026-06")

        # Verify the demo data is there
        assert len(lines) > 0, "Expected lines in 2026-06 period"

        # Check for various statuses
        estimated = [l for l in lines if l.status == AccrualStatus.ESTIMATED]
        posted = [l for l in lines if l.status == AccrualStatus.POSTED]
        cleared = [l for l in lines if l.status == AccrualStatus.CLEARED]

        # The demo should have lines in different states
        assert len(estimated) + len(posted) + len(cleared) > 0


def test_journal_entries_posted(snapshot_path):
    """Test journal entry functionality with snapshot data."""
    if not snapshot_path.exists():
        pytest.skip(f"Demo snapshot not found at {snapshot_path}")

    with IsolatedDatabaseContext(snapshot_path=snapshot_path) as db_path:
        settings = make_test_settings(db_path)
        rt = Runtime(settings)

        # Query journal entries from the demo
        jes = rt.repo.journal_entries(period="2026-06")

        # Verify the data integrity
        for je in jes:
            assert je.external_id, "JE must have external_id"
            assert je.debit_account, "JE must have debit account"
            assert je.credit_account, "JE must have credit account"
            assert je.amount > 0, "JE amount must be positive"


def test_vendor_summary(snapshot_path):
    """Test vendor grouping and summary with snapshot."""
    if not snapshot_path.exists():
        pytest.skip(f"Demo snapshot not found at {snapshot_path}")

    with IsolatedDatabaseContext(snapshot_path=snapshot_path) as db_path:
        settings = make_test_settings(db_path)
        rt = Runtime(settings)

        lines = rt.repo.lines(period="2026-06")

        # Group by vendor
        vendors = {}
        for line in lines:
            if line.vendor_name not in vendors:
                vendors[line.vendor_name] = []
            vendors[line.vendor_name].append(line)

        # Verify we have vendor data
        assert len(vendors) > 0, "Expected vendors in demo data"

        # Each vendor should have at least one line
        for vendor_name, vendor_lines in vendors.items():
            assert len(vendor_lines) > 0, f"Vendor {vendor_name} should have lines"

            # All lines from a vendor should have the same vendor_name
            for line in vendor_lines:
                assert line.vendor_name == vendor_name


def test_audit_trail_exists(snapshot_path):
    """Test that audit logs are available if included in snapshot."""
    if not snapshot_path.exists():
        pytest.skip(f"Demo snapshot not found at {snapshot_path}")

    with IsolatedDatabaseContext(snapshot_path=snapshot_path) as db_path:
        settings = make_test_settings(db_path)
        rt = Runtime(settings)

        # Try to get audit logs
        audit_rows = rt.repo.audit_rows(limit=10)

        # The snapshot may or may not include audit logs depending on export options
        # This test just ensures the query works
        assert isinstance(audit_rows, list)


# Example of a custom test using snapshot data
class TestDemoScenarios:
    """Collection of scenario tests using demo data."""

    @pytest.fixture(autouse=True)
    def setup(self, snapshot_path):
        """Set up runtime with demo snapshot."""
        if not snapshot_path.exists():
            pytest.skip(f"Demo snapshot not found at {snapshot_path}")

        self.context = IsolatedDatabaseContext(snapshot_path=snapshot_path)
        self.db_path = self.context.__enter__()
        self.settings = make_test_settings(self.db_path)
        self.rt = Runtime(self.settings)
        yield
        self.context.__exit__(None, None, None)

    def test_closed_lines_are_cleared(self):
        """Verify that closed accruals transition to cleared status."""
        lines = self.rt.repo.lines(period="2026-06")
        cleared = [l for l in lines if l.status == AccrualStatus.CLEARED]

        for line in cleared:
            assert line.cleared_invoice_amount is not None, (
                f"Cleared line {line.line_id} should have cleared_invoice_amount"
            )

    def test_held_lines_have_hold_reason(self):
        """Verify that held lines have a reason."""
        lines = self.rt.repo.lines(period="2026-06")
        held = [l for l in lines if l.status == AccrualStatus.HELD_FOR_REVIEW]

        for line in held:
            assert line.hold_reason, (
                f"Held line {line.line_id} should have hold_reason"
            )
