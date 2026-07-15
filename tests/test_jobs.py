from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.jobs import JobNotFound, JobStore
from app.models import JobStatus
from app.settings import Settings


def test_settings_use_documented_limits(tmp_path: Path) -> None:
    settings = Settings(work_root=tmp_path)

    assert settings.max_upload_bytes == 1024**3
    assert settings.max_extracted_bytes == 5 * 1024**3
    assert settings.max_entries == 10_000
    assert settings.max_nested_depth == 3
    assert settings.job_ttl_seconds == 3600


def test_job_snapshot_tracks_monotonic_progress(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    job = store.create("documents.zip")
    store.start_stage(job.id, JobStatus.CONVERTING, total=3)
    store.record_converted(job.id, "one.pdf", empty=True)

    snapshot = store.snapshot(job.id)

    assert snapshot["status"] == "converting"
    assert snapshot["processed"] == 1
    assert snapshot["converted"] == 1
    assert snapshot["empty"] == 1
    assert snapshot["progress"] == pytest.approx(1 / 3)


def test_expired_jobs_are_selected_by_last_access(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    store = JobStore(tmp_path, clock=lambda: now)
    job = store.create("old.zip")
    store.set_status(job.id, JobStatus.FAILED, error="failed")
    store.set_last_access(job.id, now - timedelta(seconds=3601))

    assert [item.id for item in store.expired(3600)] == [job.id]


def test_active_jobs_do_not_expire(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    store = JobStore(tmp_path, clock=lambda: now)
    job = store.create("active.zip")
    store.set_last_access(job.id, now - timedelta(seconds=3601))

    assert store.expired(3600) == []


def test_delete_removes_job_directory_and_lookup(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    job = store.create("delete.zip")
    (job.root / "marker").write_text("private", encoding="utf-8")
    store.set_status(job.id, JobStatus.FAILED, error="failed")

    store.delete(job.id)

    assert not job.root.exists()
    with pytest.raises(JobNotFound):
        store.snapshot(job.id)
