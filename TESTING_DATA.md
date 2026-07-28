# Testing with Standalone Accounting Data

This guide explains how to extract and use accounting data as a standalone database for testing, development, and analysis.

## Quick Start

### Export the Current Database

Export your current register to a standalone database file:

```bash
uv run accrual-agent export-db --out snapshots/my-snapshot.db
```

Options:
- `--out` — destination path (default: `snapshots/accruals-export.db`)
- `--period 2026-06` — filter to a single period (optional)
- `--no-audit` — exclude audit trail logs
- `--no-comms` — exclude communication logs

Output will show:
```
Database exported: snapshots/my-snapshot.db
  Accrual lines: 42
  Journal entries: 15
```

### List Available Snapshots

```bash
uv run accrual-agent list-snapshots --snapshots-dir snapshots/
```

Shows all `.db` files in the directory with line counts and period coverage.

### Import a Snapshot

Load a snapshot into your runtime database:

```bash
uv run accrual-agent import-db snapshots/my-snapshot.db
```

This replaces `data/accruals.db` with the snapshot. Your runtime then works with that data.

---

## Common Workflows

### Workflow 1: Create a Test Database from Demo

```bash
# Generate the standard demo data
make demo

# Export it as a reusable snapshot
uv run accrual-agent export-db --out snapshots/demo-2026-06.db --period 2026-06

# Later, load it for testing
uv run accrual-agent import-db snapshots/demo-2026-06.db

# Now your runtime has the exact demo state
uv run accrual-agent status
```

### Workflow 2: Snapshot Production Data

When you want to run analysis or tests against real live data:

```bash
# With ACCRUAL_MODE=live and real credentials:
export ACCRUAL_MODE=live
uv run accrual-agent export-db --out snapshots/prod-snapshot-2026-07.db

# Later, in a sandbox or test environment:
unset NETSUITE_TOKEN_ID  # prevent accidents
uv run accrual-agent import-db snapshots/prod-snapshot-2026-07.db
uv run accrual-agent status  # inspect it safely
```

### Workflow 3: Test-Driven Development

For a unit test that needs specific accrual state:

```python
import pytest
from pathlib import Path
from accrual_agent.register.db_export import create_test_database
from accrual_agent.runtime import Runtime
from accrual_agent.config import Settings


@pytest.fixture
def test_runtime_with_demo_data(tmp_path):
    """Create an isolated Runtime with demo data."""
    # Copy the demo snapshot into a temp directory
    demo_snapshot = Path("snapshots/demo-2026-06.db")
    if not demo_snapshot.exists():
        # Generate it if it doesn't exist
        os.system("make demo && uv run accrual-agent export-db --out snapshots/demo-2026-06.db --period 2026-06")
    
    test_db = create_test_database(
        db_path=tmp_path / "test.db",
        snapshot_path=demo_snapshot,
    )
    
    # Create a Settings object pointing to the test database
    settings = Settings()
    settings.db_path = str(test_db)
    
    return Runtime(settings)


def test_status_report(test_runtime_with_demo_data):
    """Test that the status report works with demo data."""
    rt = test_runtime_with_demo_data
    
    # Now rt has the exact state from the demo snapshot
    lines = rt.repo.lines(period="2026-06")
    assert len(lines) > 0
    
    held = [l for l in lines if l.status == AccrualStatus.HELD_FOR_REVIEW]
    # ... make your assertions
```

### Workflow 4: Minimal Period-Based Export

Export only a specific period to reduce file size:

```bash
# Create a lean 2026-06 test database (no prior periods)
uv run accrual-agent export-db \
    --out snapshots/June-only.db \
    --period 2026-06 \
    --no-audit \
    --no-comms
```

This creates a small database with:
- Only accrual lines from 2026-06
- No audit trail
- No email/comms logs
- Much smaller file size

Perfect for CI/CD or cloud test suites.

---

## Database Structure

Both exported and imported databases follow the same SQLite schema:

### Core Tables

**accrual_lines**
- Core register: vendor ID, amount, status, confirmed amounts, holds, GL mapping
- One row per accrual line (identified by `line_id`)

**journal_entries**
- Posted JEs: debit/credit accounts, amounts, subsidiary, reversals
- References accrual lines by `line_id`

**audit_log**
- Immutable changelog: every status/amount change, who made it, when
- Tracks: field, old value → new value, actor, source

**comm_log**
- Email/communication record: outbound requests, vendor replies, attachments
- Tracks: stage (request, reminder, etc.), direction (out/in), delivery status

---

## Integration with Tests

### Using the Fixture Module

Import from `tests/conftest_db_fixtures.py`:

```python
import pytest
from tests.conftest import (
    test_db_with_snapshot,
    test_repository,
    IsolatedDatabaseContext,
    make_test_settings,
)


# Minimal: start with a blank schema
def test_something(test_repository):
    # test_repository is a Repository instance with empty tables
    line = test_repository.upsert_line(...)


# With snapshot data
def test_with_demo(tmp_path, test_db_with_snapshot):
    # Load demo-2026-06.db into tmp_path/test.db
    snapshot = Path("snapshots/demo-2026-06.db")
    db = test_db_with_snapshot(tmp_path, snapshot)
    
    rt = Runtime(make_test_settings(db))
    # Now rt has demo state


# Context manager style
def test_isolated_work():
    with IsolatedDatabaseContext(snapshot_path=Path("snapshots/demo-2026-06.db")) as db_path:
        rt = Runtime(make_test_settings(db_path))
        # work with rt
        # database is cleaned up automatically on exit
```

---

## File Organization

Recommended structure:

```
NS_Zip-Accrual-Demo/
  data/
    accruals.db                 # Runtime database (gitignore)
  
  snapshots/
    demo-2026-06.db             # Committed test snapshot (small, for CI)
    demo-2026-06-with-audit.db  # Full snapshot (large, for detailed testing)
    prod-2026-06.db             # (Gitignore if real data)
  
  tests/
    conftest_db_fixtures.py     # Fixture library
    test_example.py
```

Add to `.gitignore`:

```
# Runtime database
data/accruals.db
data/*.db

# Local snapshots (keep small committed ones, ignore large/sensitive ones)
snapshots/prod-*.db
snapshots/*-with-comms-*.db
```

---

## Best Practices

### 1. Keep Test Snapshots Small
Use `--period` and `--no-audit --no-comms` to reduce snapshot size:

```bash
uv run accrual-agent export-db \
    --out snapshots/demo.db \
    --period 2026-06 \
    --no-audit \
    --no-comms
```

### 2. Version Your Snapshots
Include the period in the filename:

```
snapshots/
  demo-2026-06.db           # A predictable demo
  prod-snapshot-2026-07.db  # Dated production export
```

### 3. Test Isolation
Always use `--temp-db-dir` or `IsolatedDatabaseContext` in tests so each test gets its own copy:

```python
with IsolatedDatabaseContext(snapshot_path=Path("snapshots/demo-2026-06.db")) as db:
    # Each test gets an isolated copy
    rt = Runtime(make_test_settings(db))
```

### 4. Document Snapshot Contents
Add a README in `snapshots/`:

```markdown
# Snapshots

## demo-2026-06.db
- **Source**: `make demo` from main branch
- **Content**: 42 accrual lines, 3 periods (2026-04, 2026-05, 2026-06)
- **Use**: Test suite baseline, manual testing
- **Includes**: Audit trail, comms, trust history

## demo-lean.db
- **Content**: Only 2026-06, no audit/comms
- **Use**: CI/CD tests (small file)
- **Size**: ~50 KB
```

### 5. CI/CD Integration
In your GitHub Actions or similar:

```yaml
- name: Export test database
  run: |
    make demo
    mkdir -p snapshots
    uv run accrual-agent export-db \
      --out snapshots/demo-ci.db \
      --period 2026-06 \
      --no-audit --no-comms

- name: Run tests with snapshot
  run: |
    pytest tests/ -m integration
  env:
    ACCRUAL_DB_SNAPSHOT: snapshots/demo-ci.db
```

---

## Troubleshooting

### "Source database not found"
The export path you're trying to read doesn't exist. Run `make demo` first, or check that your `ACCRUAL_DB_PATH` is set correctly.

### "Merge conflict" on committed snapshots
If you export snapshots frequently, they'll change and cause git conflicts. Keep the committed snapshot stable and use dated exports for production data:

```bash
# Small, stable, committed
snapshots/demo-2026-06.db

# Dated, possibly gitignored
snapshots/prod-snapshot-2026-07-28.db
```

### Database is locked
If you get "database is locked" errors:
1. Ensure no other process is using the database
2. Delete `data/accruals.db-wal` and `data/accruals.db-shm` (WAL temp files)
3. Try again

---

## Advanced: Custom Snapshots

You can create custom test data by importing a snapshot, running specific cycles, and exporting again:

```bash
# Start with demo
uv run accrual-agent import-db snapshots/demo-2026-06.db

# Run through close day 5 scenario
uv run accrual-agent run-cycle --close-day 5

# Export the updated state
uv run accrual-agent export-db --out snapshots/demo-through-day5.db

# Now snapshots/demo-through-day5.db has the exact state after day 5
```

This is useful for testing specific scenarios (e.g., "what happens if this vendor never responds?").
