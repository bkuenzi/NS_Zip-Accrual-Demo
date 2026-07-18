"""Process-level advisory lock: one mutating command at a time.

SQLite is single-writer; run-cycle, poll-inbox, and review approvals mutate the
same register, so mutating commands take this lock, wait briefly, then exit
with a clear message instead of interleaving.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from pathlib import Path


class LockBusyError(RuntimeError):
    pass


@contextlib.contextmanager
def advisory_lock(db_path: str | Path, timeout_s: float = 10.0) -> Iterator[None]:
    lock_path = Path(str(db_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            # Stale-lock recovery: a crashed process leaves the file behind.
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > 3600:
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise LockBusyError(
                    "another accrual-agent run is in progress "
                    f"(lock: {lock_path}); retry shortly"
                ) from None
            time.sleep(0.25)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock_path.unlink(missing_ok=True)
