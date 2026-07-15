import shutil
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from app.models import ConversionIssue, Job, JobStatus


class JobNotFound(KeyError):
    pass


class JobBusy(RuntimeError):
    pass


Clock = Callable[[], datetime]
ACTIVE_STATUSES = {
    JobStatus.UPLOADING,
    JobStatus.QUEUED,
    JobStatus.EXTRACTING,
    JobStatus.CONVERTING,
    JobStatus.PACKAGING,
}


class JobStore:
    def __init__(self, root: Path, clock: Clock | None = None) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._jobs: dict[str, Job] = {}
        self._lock = RLock()

    def create(self, original_name: str) -> Job:
        with self._lock:
            now = self._clock()
            job_id = str(uuid4())
            job_root = self.root / job_id
            job_root.mkdir(parents=True)
            job = Job(
                id=job_id,
                original_name=original_name,
                root=job_root,
                created_at=now,
                last_accessed_at=now,
            )
            self._jobs[job_id] = job
            return job

    def get(self, job_id: str, *, touch: bool = True) -> Job:
        with self._lock:
            try:
                job = self._jobs[job_id]
            except KeyError as error:
                raise JobNotFound(job_id) from error
            if touch:
                job.last_accessed_at = self._clock()
            return job

    def snapshot(self, job_id: str) -> dict[str, object]:
        with self._lock:
            job = self.get(job_id)
            progress = min(1.0, job.processed / job.total) if job.total else 0.0
            if job.status is JobStatus.COMPLETE:
                progress = 1.0
            return {
                "id": job.id,
                "original_name": job.original_name,
                "status": job.status.value,
                "current_file": job.current_file,
                "total": job.total,
                "processed": job.processed,
                "converted": job.converted,
                "empty": job.empty,
                "skipped": job.skipped,
                "failed": job.failed,
                "progress": progress,
                "error": job.error,
                "has_issues": bool(job.issues),
                "download_available": (
                    job.status is JobStatus.COMPLETE and job.result_path is not None
                ),
            }

    def set_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error: str | None = None,
    ) -> None:
        with self._lock:
            job = self.get(job_id, touch=False)
            job.status = status
            job.error = error

    def start_stage(self, job_id: str, status: JobStatus, *, total: int = 0) -> None:
        with self._lock:
            job = self.get(job_id, touch=False)
            job.status = status
            if total:
                job.total = total
            job.current_file = None

    def set_current_file(self, job_id: str, relative_path: str) -> None:
        with self._lock:
            self.get(job_id, touch=False).current_file = relative_path

    def record_converted(
        self,
        job_id: str,
        relative_path: str,
        *,
        empty: bool = False,
    ) -> None:
        with self._lock:
            job = self.get(job_id, touch=False)
            job.current_file = relative_path
            job.converted += 1
            job.empty += int(empty)
            job.processed += 1

    def record_issue(self, job_id: str, issue: ConversionIssue) -> None:
        with self._lock:
            job = self.get(job_id, touch=False)
            job.current_file = issue.relative_path
            job.issues.append(issue)
            if issue.outcome == "skipped":
                job.skipped += 1
            else:
                job.failed += 1
            job.processed += 1

    def set_result(self, job_id: str, result_path: Path) -> None:
        with self._lock:
            job = self.get(job_id, touch=False)
            job.result_path = result_path
            job.current_file = None
            job.status = JobStatus.COMPLETE

    def set_last_access(self, job_id: str, value: datetime) -> None:
        with self._lock:
            self.get(job_id, touch=False).last_accessed_at = value

    def expired(self, ttl_seconds: int) -> list[Job]:
        with self._lock:
            cutoff = self._clock() - timedelta(seconds=ttl_seconds)
            return [
                job
                for job in self._jobs.values()
                if job.status in {JobStatus.COMPLETE, JobStatus.FAILED}
                and job.last_accessed_at < cutoff
            ]

    def delete(self, job_id: str, *, allow_active: bool = False) -> None:
        with self._lock:
            job = self.get(job_id, touch=False)
            if job.status in ACTIVE_STATUSES and not allow_active:
                raise JobBusy(job_id)
            job.status = JobStatus.DELETING
            root = job.root
        try:
            shutil.rmtree(root)
        except FileNotFoundError:
            pass
        finally:
            with self._lock:
                self._jobs.pop(job_id, None)
