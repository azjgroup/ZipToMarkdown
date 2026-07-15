import asyncio
import logging
import shutil
from pathlib import Path

from app.jobs import JobStore
from app.models import JobStatus

logger = logging.getLogger(__name__)


def clear_startup_workspace(work_root: Path) -> None:
    work_root.mkdir(parents=True, exist_ok=True)
    for child in work_root.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        except FileNotFoundError:
            continue


def cleanup_expired_jobs(store: JobStore, ttl_seconds: int) -> int:
    removed = 0
    for job in store.expired(ttl_seconds):
        try:
            store.set_status(job.id, JobStatus.EXPIRED)
            store.delete(job.id, allow_active=True)
            removed += 1
        except OSError:
            logger.exception("Could not remove expired job %s", job.id)
    return removed


async def cleanup_loop(
    store: JobStore,
    ttl_seconds: int,
    interval_seconds: int = 60,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await asyncio.to_thread(cleanup_expired_jobs, store, ttl_seconds)
