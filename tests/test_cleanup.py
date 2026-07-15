from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.cleanup import cleanup_expired_jobs, clear_startup_workspace
from app.jobs import JobStore
from app.models import JobStatus


def test_startup_cleanup_removes_children_and_preserves_root(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    (root / "old" / "nested").mkdir(parents=True)
    (root / "old" / "nested" / "private.txt").write_text(
        "private",
        encoding="utf-8",
    )

    clear_startup_workspace(root)

    assert root.is_dir()
    assert list(root.iterdir()) == []


def test_expiry_cleanup_deletes_only_expired_terminal_jobs(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    store = JobStore(tmp_path / "jobs", clock=lambda: now)
    expired = store.create("expired.zip")
    current = store.create("current.zip")
    active = store.create("active.zip")
    store.set_status(expired.id, JobStatus.COMPLETE)
    store.set_status(current.id, JobStatus.COMPLETE)
    store.set_last_access(expired.id, now - timedelta(seconds=3_601))
    store.set_last_access(active.id, now - timedelta(seconds=3_601))

    removed = cleanup_expired_jobs(store, 3_600)

    assert removed == 1
    assert not expired.root.exists()
    assert current.root.exists()
    assert active.root.exists()


def test_expiry_cleanup_keeps_failed_removal_available_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    store = JobStore(tmp_path / "jobs", clock=lambda: now)
    job = store.create("retry.zip")
    store.set_status(job.id, JobStatus.FAILED, error="failed")
    store.set_last_access(job.id, now - timedelta(seconds=3_601))

    def fail_removal(_: Path) -> None:
        raise OSError("disk is busy")

    monkeypatch.setattr("app.jobs.shutil.rmtree", fail_removal)

    assert cleanup_expired_jobs(store, 3_600) == 0
    assert store.snapshot(job.id)["status"] == "expired"
