from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class JobStatus(str, Enum):
    UPLOADING = "uploading"
    QUEUED = "queued"
    EXTRACTING = "extracting"
    CONVERTING = "converting"
    PACKAGING = "packaging"
    COMPLETE = "complete"
    FAILED = "failed"
    DELETING = "deleting"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class ConversionIssue:
    relative_path: str
    outcome: str
    reason: str


@dataclass(slots=True)
class Job:
    id: str
    original_name: str
    root: Path
    status: JobStatus = JobStatus.UPLOADING
    current_file: str | None = None
    total: int = 0
    processed: int = 0
    converted: int = 0
    empty: int = 0
    skipped: int = 0
    failed: int = 0
    issues: list[ConversionIssue] = field(default_factory=list)
    result_path: Path | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
