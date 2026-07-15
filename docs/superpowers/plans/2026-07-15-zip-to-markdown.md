# Zip to Markdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a double-clickable, local-only Mac web app that safely converts the contents of ZIP archives into a downloadable Markdown-only ZIP using Microsoft MarkItDown.

**Architecture:** A FastAPI process bound to loopback streams uploads into isolated job directories, queues one background conversion at a time, safely expands archives, converts each regular file through a timeout-isolated MarkItDown subprocess, and packages only Markdown output. A framework-free browser client shows upload and conversion progress; a locked in-memory job store and periodic cleanup service own lifecycle state.

**Tech Stack:** Python 3.10+ (verified on 3.12), FastAPI, Uvicorn, Jinja2, vanilla JavaScript/CSS, Microsoft MarkItDown 0.1.6, pytest, HTTPX, Playwright, Ruff

---

## File responsibility map

- `pyproject.toml`: package metadata, direct pinned dependencies, test tools, pytest and Ruff configuration.
- `requirements.lock`: fully resolved runtime dependency lock used by the Mac launcher.
- `requirements-dev.lock`: fully resolved runtime and test dependency lock used by contributors.
- `app/settings.py`: immutable limits and work-directory configuration.
- `app/models.py`: job states, conversion issues, conversion outcomes, and JSON snapshots.
- `app/jobs.py`: thread-safe creation, lookup, mutation, expiry, and deletion of jobs.
- `app/archives.py`: streamed, bounded, traversal-safe extraction and nested-ZIP expansion.
- `app/converter_worker.py`: narrow child-process entry point that calls `MarkItDown.convert_local()`.
- `app/conversion.py`: timeout-controlled subprocess orchestration and Markdown output validation.
- `app/results.py`: sanitized failure report generation and Markdown-only result packaging.
- `app/processing.py`: extraction → conversion → packaging pipeline and progress updates.
- `app/cleanup.py`: startup cleanup and periodic expiry removal.
- `app/main.py`: dependency wiring, lifespan, local API, template, and static routes.
- `app/templates/index.html`: semantic single-page UI structure.
- `app/static/app.css`: approved visual direction, accessibility, narrow-window, and reduced-motion styles.
- `app/static/app.js`: drag-and-drop, upload progress, polling, completion, download, and deletion states.
- `Start App.command`: environment setup, dependency fingerprinting, local port selection, health wait, browser launch, and server shutdown.
- `tests/`: focused unit, API, real-converter, pipeline, and browser tests.
- `README.md`: setup, operation, privacy, limits, supported behavior, and troubleshooting.
- `LICENSE`: MIT license.

## Task 1: Bootstrap the Python project and locked environment

**Files:**

- Create: `pyproject.toml`
- Create: `app/__init__.py`
- Create: `tests/__init__.py`
- Create: `.gitignore`
- Generate: `requirements.lock`
- Generate: `requirements-dev.lock`

- [ ] **Step 1: Add package and tool configuration**

Create `pyproject.toml` with exact direct dependencies:

```toml
[build-system]
requires = ["setuptools==80.9.0"]
build-backend = "setuptools.build_meta"

[project]
name = "zip-to-markdown"
version = "0.1.0"
description = "A local Mac web app for converting ZIP contents to Markdown"
requires-python = ">=3.10,<3.14"
dependencies = [
  "fastapi==0.116.1",
  "jinja2==3.1.6",
  "markitdown[all]==0.1.6",
  "python-multipart==0.0.20",
  "uvicorn==0.35.0",
]

[project.optional-dependencies]
test = [
  "httpx==0.28.1",
  "playwright==1.54.0",
  "pytest==8.4.1",
  "pytest-asyncio==1.1.0",
  "ruff==0.12.7",
]

[tool.setuptools.packages.find]
include = ["app*"]

[tool.pytest.ini_options]
addopts = "-ra"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
```

Create empty `app/__init__.py` and `tests/__init__.py`. Create `.gitignore`:

```gitignore
.DS_Store
.venv/
.pytest_cache/
.ruff_cache/
__pycache__/
*.py[cod]
playwright-report/
test-results/
```

- [ ] **Step 2: Create and populate the development environment**

Run:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip pip-tools
.venv/bin/pip-compile --extra test pyproject.toml --output-file requirements-dev.lock
.venv/bin/pip-compile pyproject.toml --output-file requirements.lock
.venv/bin/python -m pip install -r requirements-dev.lock
```

Expected: both lock files are created; installation exits `0`; `.venv/bin/python` imports FastAPI and MarkItDown.

- [ ] **Step 3: Verify dependency versions and project imports**

Run:

```bash
.venv/bin/python -c "import fastapi, markitdown; print(fastapi.__version__)"
.venv/bin/ruff check app tests
```

Expected: FastAPI prints `0.116.1`; Ruff reports `All checks passed!`.

- [ ] **Step 4: Commit the bootstrap**

```bash
git add pyproject.toml requirements.lock requirements-dev.lock app/__init__.py tests/__init__.py .gitignore
git commit -m "build: bootstrap local conversion app"
```

## Task 2: Add settings and a thread-safe job store

**Files:**

- Create: `app/settings.py`
- Create: `app/models.py`
- Create: `app/jobs.py`
- Create: `tests/test_jobs.py`

- [ ] **Step 1: Write failing tests for defaults, state snapshots, expiry, and deletion**

Create `tests/test_jobs.py`:

```python
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


def test_delete_removes_job_directory_and_lookup(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    job = store.create("delete.zip")
    (job.root / "marker").write_text("private", encoding="utf-8")
    store.delete(job.id)
    assert not job.root.exists()
    with pytest.raises(JobNotFound):
        store.snapshot(job.id)
```

- [ ] **Step 2: Run the job tests and confirm RED**

Run: `.venv/bin/pytest tests/test_jobs.py -v`

Expected: collection fails with `ModuleNotFoundError` for `app.jobs`.

- [ ] **Step 3: Implement immutable settings and shared models**

Create `app/settings.py`:

```python
from dataclasses import dataclass
from pathlib import Path
from tempfile import gettempdir


@dataclass(frozen=True, slots=True)
class Settings:
    work_root: Path = Path(gettempdir()) / "zip-to-markdown-jobs"
    max_upload_bytes: int = 1024**3
    max_extracted_bytes: int = 5 * 1024**3
    max_entries: int = 10_000
    max_nested_depth: int = 3
    max_path_length: int = 1_024
    job_ttl_seconds: int = 3_600
    conversion_timeout_seconds: int = 900
    chunk_size: int = 1024 * 1024
```

Create `app/models.py` with the exact public types used by later tasks:

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path


class JobStatus(StrEnum):
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
```

- [ ] **Step 4: Implement the locked job store**

Create `app/jobs.py` with `JobNotFound`, `JobBusy`, and a locked `JobStore`:

```python
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
                "download_available": job.status is JobStatus.COMPLETE and job.result_path is not None,
            }

    def set_status(self, job_id: str, status: JobStatus, *, error: str | None = None) -> None:
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

    def record_converted(self, job_id: str, relative_path: str, *, empty: bool = False) -> None:
        with self._lock:
            job = self.get(job_id, touch=False)
            job.current_file = relative_path
            job.converted += 1
            job.empty += int(empty)
            job.processed += 1

    def record_issue(self, job_id: str, issue: ConversionIssue) -> None:
        with self._lock:
            job = self.get(job_id, touch=False)
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
            shutil.rmtree(root, ignore_errors=False)
        except FileNotFoundError:
            pass
        finally:
            with self._lock:
                self._jobs.pop(job_id, None)
```

`snapshot()` returns only browser-safe values and clamps progress to the inclusive range from zero to one. `record_issue()` increments `skipped` for outcome `skipped`, otherwise `failed`, and always increments `processed`.

- [ ] **Step 5: Run the focused and full suites**

Run:

```bash
.venv/bin/pytest tests/test_jobs.py -v
.venv/bin/pytest -q
.venv/bin/ruff check app tests
```

Expected: four job tests pass; full suite passes; Ruff is clean.

- [ ] **Step 6: Commit the job foundation**

```bash
git add app/settings.py app/models.py app/jobs.py tests/test_jobs.py
git commit -m "feat: add conversion job lifecycle"
```

## Task 3: Safely extract outer and nested ZIP archives

**Files:**

- Create: `app/archives.py`
- Create: `tests/test_archives.py`

- [ ] **Step 1: Write failing tests for safe extraction and stable nested paths**

Create `tests/test_archives.py` with reusable in-memory ZIP helpers and the first behavior test:

```python
import io
import stat
from dataclasses import replace
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from app.archives import ArchiveExtractor, ArchiveLimitError
from app.settings import Settings


def make_zip_bytes(entries: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue()


def write_zip(path: Path, entries: dict[str, bytes]) -> Path:
    path.write_bytes(make_zip_bytes(entries))
    return path


def test_settings(tmp_path: Path) -> Settings:
    return Settings(work_root=tmp_path / "jobs")


def test_extracts_regular_and_nested_files(tmp_path: Path) -> None:
    nested = make_zip_bytes({"notes.txt": b"nested"})
    archive = write_zip(tmp_path / "input.zip", {
        "docs/report.pdf": b"pdf",
        "bundle.zip": nested,
        "__MACOSX/._report": b"metadata",
    })
    result = ArchiveExtractor(test_settings(tmp_path)).extract(archive, tmp_path / "out")
    assert [path.relative_to(tmp_path / "out").as_posix() for path in result.files] == [
        "bundle.zip.contents/notes.txt",
        "docs/report.pdf",
    ]
    assert result.issues == []
```

- [ ] **Step 2: Write failing tests for traversal, links, and global limits**

Add separate tests for traversal, links, byte limits, entry limits, and depth limits:

```python
def test_rejects_traversal_without_stopping_safe_entries(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "paths.zip", {
        "../escape.txt": b"escape",
        "safe.txt": b"safe",
    })
    result = ArchiveExtractor(test_settings(tmp_path)).extract(archive, tmp_path / "out")
    assert [path.name for path in result.files] == ["safe.txt"]
    assert result.issues[0].outcome == "skipped"
    assert not (tmp_path / "escape.txt").exists()


def test_rejects_symbolic_links(tmp_path: Path) -> None:
    archive = tmp_path / "link.zip"
    info = ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with ZipFile(archive, "w") as zipped:
        zipped.writestr(info, "target")
    result = ArchiveExtractor(test_settings(tmp_path)).extract(archive, tmp_path / "out")
    assert result.files == []
    assert result.issues[0].reason == "Symbolic links are not extracted."


def test_enforces_actual_extracted_byte_limit(tmp_path: Path) -> None:
    settings = replace(test_settings(tmp_path), max_extracted_bytes=4)
    archive = write_zip(tmp_path / "large.zip", {"five.txt": b"12345"})
    with pytest.raises(ArchiveLimitError, match="extracted byte limit"):
        ArchiveExtractor(settings).extract(archive, tmp_path / "out")


def test_enforces_global_entry_limit(tmp_path: Path) -> None:
    settings = replace(test_settings(tmp_path), max_entries=1)
    archive = write_zip(tmp_path / "entries.zip", {"one": b"1", "two": b"2"})
    with pytest.raises(ArchiveLimitError, match="entry limit"):
        ArchiveExtractor(settings).extract(archive, tmp_path / "out")


def test_skips_zip_beyond_nested_depth(tmp_path: Path) -> None:
    payload = make_zip_bytes({"deep.txt": b"deep"})
    for level in range(4, 0, -1):
        payload = make_zip_bytes({f"level-{level}.zip": payload})
    archive = tmp_path / "deep.zip"
    archive.write_bytes(payload)
    result = ArchiveExtractor(test_settings(tmp_path)).extract(archive, tmp_path / "out")
    assert result.files == []
    assert result.issues[-1].reason == "Nested ZIP depth exceeds the limit of 3."
```

- [ ] **Step 3: Run archive tests and confirm RED**

Run: `.venv/bin/pytest tests/test_archives.py -v`

Expected: collection fails because `app.archives` does not exist.

- [ ] **Step 4: Implement the extractor and explicit result types**

Create `app/archives.py`:

```python
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from zipfile import BadZipFile, ZipFile, ZipInfo, is_zipfile

from app.models import ConversionIssue
from app.settings import Settings


class ArchiveError(ValueError):
    pass


class ArchiveLimitError(ArchiveError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    files: list[Path]
    issues: list[ConversionIssue]


@dataclass(slots=True)
class _Counters:
    entries: int = 0
    bytes_written: int = 0


class ArchiveExtractor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def extract(self, archive_path: Path, destination: Path) -> ExtractionResult:
        if not is_zipfile(archive_path):
            raise ArchiveError("The uploaded file is not a valid ZIP archive.")
        destination.mkdir(parents=True, exist_ok=False)
        files: list[Path] = []
        issues: list[ConversionIssue] = []
        self._extract_zip(archive_path, destination, 0, _Counters(), files, issues)
        files.sort(key=lambda path: path.relative_to(destination).as_posix())
        return ExtractionResult(files=files, issues=issues)

    def _extract_zip(
        self,
        archive_path: Path,
        destination: Path,
        depth: int,
        counters: _Counters,
        files: list[Path],
        issues: list[ConversionIssue],
    ) -> None:
        try:
            archive = ZipFile(archive_path)
        except BadZipFile as error:
            raise ArchiveError("The uploaded ZIP archive is malformed.") from error
        with archive:
            for info in archive.infolist():
                counters.entries += 1
                if counters.entries > self.settings.max_entries:
                    raise ArchiveLimitError("The archive exceeds the entry limit.")
                display_name = info.filename.replace("\n", " ").replace("\r", " ")
                try:
                    relative = self._safe_relative_path(info, destination)
                except ArchiveError as error:
                    issues.append(ConversionIssue(display_name, "skipped", str(error)))
                    continue
                if self._is_metadata(relative) or info.is_dir():
                    continue
                mode = info.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if stat.S_ISLNK(mode):
                    issues.append(
                        ConversionIssue(relative.as_posix(), "skipped", "Symbolic links are not extracted.")
                    )
                    continue
                if file_type not in {0, stat.S_IFREG}:
                    issues.append(
                        ConversionIssue(relative.as_posix(), "skipped", "Non-regular entries are not extracted.")
                    )
                    continue
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with archive.open(info) as source, target.open("xb") as output:
                        while chunk := source.read(self.settings.chunk_size):
                            output.write(chunk)
                            counters.bytes_written += len(chunk)
                            if counters.bytes_written > self.settings.max_extracted_bytes:
                                raise ArchiveLimitError("The archive exceeds the extracted byte limit.")
                except ArchiveLimitError:
                    target.unlink(missing_ok=True)
                    raise
                if target.suffix.lower() != ".zip":
                    files.append(target)
                    continue
                if depth >= self.settings.max_nested_depth:
                    issues.append(
                        ConversionIssue(
                            relative.as_posix(),
                            "skipped",
                            f"Nested ZIP depth exceeds the limit of {self.settings.max_nested_depth}.",
                        )
                    )
                    target.unlink(missing_ok=True)
                    continue
                nested_destination = target.with_name(f"{target.name}.contents")
                nested_destination.mkdir(parents=True, exist_ok=False)
                try:
                    self._extract_zip(
                        target,
                        nested_destination,
                        depth + 1,
                        counters,
                        files,
                        issues,
                    )
                except ArchiveLimitError:
                    shutil.rmtree(nested_destination, ignore_errors=True)
                    raise
                except ArchiveError as error:
                    shutil.rmtree(nested_destination, ignore_errors=True)
                    issues.append(ConversionIssue(relative.as_posix(), "skipped", str(error)))
                finally:
                    target.unlink(missing_ok=True)

    def _safe_relative_path(self, info: ZipInfo, destination: Path) -> Path:
        name = info.filename.replace("\\", "/")
        if "\x00" in name:
            raise ArchiveError("NUL bytes are not allowed in archive paths.")
        pure_path = PurePosixPath(name)
        if pure_path.is_absolute() or PureWindowsPath(name).drive or ".." in pure_path.parts:
            raise ArchiveError("Unsafe archive path was rejected.")
        if len(pure_path.as_posix()) > self.settings.max_path_length:
            raise ArchiveError("Archive path exceeds the path-length limit.")
        relative = Path(*pure_path.parts)
        resolved_destination = destination.resolve()
        if not (resolved_destination / relative).resolve().is_relative_to(resolved_destination):
            raise ArchiveError("Unsafe archive path was rejected.")
        return relative

    @staticmethod
    def _is_metadata(relative: Path) -> bool:
        return relative.name == ".DS_Store" or "__MACOSX" in relative.parts
```

- [ ] **Step 5: Run archive tests and security regression suite**

Run:

```bash
.venv/bin/pytest tests/test_archives.py -v
.venv/bin/pytest -q
.venv/bin/ruff check app tests
```

Expected: traversal and link fixtures never escape the destination; all archive tests and the full suite pass.

- [ ] **Step 6: Commit safe extraction**

```bash
git add app/archives.py tests/test_archives.py
git commit -m "feat: safely extract nested archives"
```

## Task 4: Convert files through an isolated local MarkItDown worker

**Files:**

- Create: `app/converter_worker.py`
- Create: `app/conversion.py`
- Create: `tests/test_conversion.py`

- [ ] **Step 1: Write failing converter tests**

Create `tests/test_conversion.py`. Inject a fake subprocess runner so each test exercises `LocalConverter` without launching MarkItDown:

```python
import subprocess
from pathlib import Path
from subprocess import CompletedProcess

from app.conversion import LocalConverter


def test_converter_promotes_worker_output(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    output = tmp_path / "out" / "input.txt.md"
    source.write_text("hello", encoding="utf-8")

    def successful_runner(command: list[str], **_: object) -> CompletedProcess[str]:
        Path(command[-2]).write_text("# Hello\n", encoding="utf-8")
        return CompletedProcess(command, 0)

    result = LocalConverter(timeout_seconds=1, runner=successful_runner).convert(source, output)
    assert result.outcome == "converted"
    assert output.read_text(encoding="utf-8") == "# Hello\n"


def test_converter_returns_sanitized_failure(tmp_path: Path) -> None:
    source = tmp_path / "input.bin"
    output = tmp_path / "out" / "input.bin.md"
    source.write_bytes(b"binary")

    def failing_runner(command: list[str], **_: object) -> CompletedProcess[str]:
        Path(command[-1]).write_text("UnsupportedFormat: cannot convert", encoding="utf-8")
        return CompletedProcess(command, 1)

    result = LocalConverter(timeout_seconds=1, runner=failing_runner).convert(source, output)
    assert result.outcome == "failed"
    assert result.reason == "UnsupportedFormat: cannot convert"
    assert not output.exists()


def test_converter_reports_timeout(tmp_path: Path) -> None:
    source = tmp_path / "slow.txt"
    output = tmp_path / "out" / "slow.txt.md"
    source.write_text("slow", encoding="utf-8")

    def timeout_runner(*_: object, **__: object) -> CompletedProcess[str]:
        raise subprocess.TimeoutExpired("worker", 1)

    result = LocalConverter(timeout_seconds=1, runner=timeout_runner).convert(source, output)
    assert result.reason == "Conversion exceeded the 1-second time limit."
```

- [ ] **Step 2: Run converter tests and confirm RED**

Run: `.venv/bin/pytest tests/test_conversion.py -v`

Expected: collection fails because `app.conversion` does not exist.

- [ ] **Step 3: Implement the narrow MarkItDown worker**

Create `app/converter_worker.py`:

```python
import sys
from pathlib import Path

from markitdown import MarkItDown


def _normalize_markdown(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return f"{normalized.rstrip()}\n" if normalized.strip() else ""


def _sanitize_error(error: Exception, source: Path) -> str:
    reason = f"{type(error).__name__}: {error}"
    reason = reason.replace(str(source), "<input>").replace("\n", " ").replace("\r", " ")
    return reason[:1_000]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return 2
    source, output, error_path = map(Path, argv)
    try:
        result = MarkItDown(enable_plugins=False).convert_local(str(source))
        output.write_text(_normalize_markdown(result.text_content or ""), encoding="utf-8")
        return 0
    except Exception as error:
        error_path.write_text(_sanitize_error(error, source), encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Implement timeout-controlled orchestration**

Create `app/conversion.py`:

```python
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class ConversionResult:
    outcome: str
    reason: str | None = None
    empty: bool = False


class Runner(Protocol):
    def __call__(
        self,
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        pass


class LocalConverter:
    def __init__(self, timeout_seconds: int, runner: Runner = subprocess.run) -> None:
        self.timeout_seconds = timeout_seconds
        self.runner = runner

    def convert(self, source: Path, output: Path) -> ConversionResult:
        output.parent.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex
        partial = output.with_name(f".{output.name}.{token}.partial")
        error_path = output.with_name(f".{output.name}.{token}.error")
        command = [
            sys.executable,
            "-m",
            "app.converter_worker",
            str(source),
            str(partial),
            str(error_path),
        ]
        try:
            completed = self.runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            if completed.returncode != 0:
                reason = error_path.read_text(encoding="utf-8") if error_path.exists() else completed.stderr
                return ConversionResult("failed", self._sanitize_reason(reason, source))
            if not partial.exists():
                return ConversionResult("failed", "The converter produced no output file.")
            empty = not partial.read_text(encoding="utf-8").strip()
            partial.replace(output)
            return ConversionResult("converted", empty=empty)
        except subprocess.TimeoutExpired:
            return ConversionResult(
                "failed",
                f"Conversion exceeded the {self.timeout_seconds}-second time limit.",
            )
        except (OSError, UnicodeError) as error:
            return ConversionResult("failed", self._sanitize_reason(str(error), source))
        finally:
            partial.unlink(missing_ok=True)
            error_path.unlink(missing_ok=True)

    @staticmethod
    def _sanitize_reason(reason: str | None, source: Path) -> str:
        cleaned = (reason or "The converter exited without an explanation.").strip()
        cleaned = cleaned.replace(str(source), "<input>").replace("\n", " ").replace("\r", " ")
        return cleaned[:1_000]
```

- [ ] **Step 5: Run unit tests and a real local smoke conversion**

Run:

```bash
.venv/bin/pytest tests/test_conversion.py -v
tmpdir=$(mktemp -d)
printf 'Hello MarkItDown\n' > "$tmpdir/input.txt"
.venv/bin/python -m app.converter_worker "$tmpdir/input.txt" "$tmpdir/output.md" "$tmpdir/error.txt"
test -s "$tmpdir/output.md"
.venv/bin/pytest -q
```

Expected: converter tests pass; the worker creates non-empty Markdown; the full suite passes.

- [ ] **Step 6: Commit local conversion**

```bash
git add app/converter_worker.py app/conversion.py tests/test_conversion.py
git commit -m "feat: convert files with local MarkItDown worker"
```

## Task 5: Build sanitized reports and Markdown-only result archives

**Files:**

- Create: `app/results.py`
- Create: `tests/test_results.py`

- [ ] **Step 1: Write failing report and packaging tests**

Create `tests/test_results.py`:

```python
from pathlib import Path
from zipfile import ZipFile

from app.models import ConversionIssue
from app.results import build_result_archive, write_conversion_report


def test_report_is_created_only_for_issues(tmp_path: Path) -> None:
    assert write_conversion_report(tmp_path, "input.zip", [], 2) is None
    report = write_conversion_report(
        tmp_path,
        "input.zip",
        [ConversionIssue("bad.bin", "failed", "/private/tmp/job secret\ntrace")],
        1,
    )
    text = report.read_text(encoding="utf-8")
    assert "bad.bin" in text
    assert "/private/tmp" not in text
    assert "trace" not in text


def test_result_archive_contains_only_markdown(tmp_path: Path) -> None:
    output = tmp_path / "output"
    (output / "docs").mkdir(parents=True)
    (output / "docs/report.pdf.md").write_text("report", encoding="utf-8")
    (output / "accidental.bin").write_bytes(b"never package")
    archive = build_result_archive(output, tmp_path / "result.zip")
    with ZipFile(archive) as result:
        assert result.namelist() == ["docs/report.pdf.md"]
```

- [ ] **Step 2: Run result tests and confirm RED**

Run: `.venv/bin/pytest tests/test_results.py -v`

Expected: collection fails because `app.results` does not exist.

- [ ] **Step 3: Implement report generation and packaging**

Create `app/results.py` with:

```python
import re
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.models import ConversionIssue


def output_relative_path(source_relative_path: Path) -> Path:
    return Path(f"{source_relative_path.as_posix()}.md")


def write_conversion_report(
    output_root: Path,
    original_name: str,
    issues: list[ConversionIssue],
    converted_count: int,
) -> Path | None:
    if not issues:
        return None
    report_path = output_root / "conversion-report.md"
    skipped_count = sum(issue.outcome == "skipped" for issue in issues)
    failed_count = len(issues) - skipped_count
    lines = [
        "# Conversion report",
        "",
        f"- Archive: `{_inline(original_name)}`",
        f"- Completed: {datetime.now(timezone.utc).isoformat()}",
        f"- Converted: {converted_count}",
        f"- Skipped: {skipped_count}",
        f"- Failed: {failed_count}",
        "",
        "| File | Outcome | Reason |",
        "| --- | --- | --- |",
    ]
    for issue in issues:
        lines.append(
            f"| {_cell(issue.relative_path)} | {_cell(issue.outcome)} | {_cell(issue.reason)} |"
        )
    lines.extend(
        [
            "",
            "Processing stayed on this Mac. Original files are not included in the result.",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def build_result_archive(output_root: Path, archive_path: Path) -> Path:
    markdown_files = sorted(
        (
            path
            for path in output_root.rglob("*")
            if path.is_file() and path.suffix.lower() == ".md"
        ),
        key=lambda path: path.relative_to(output_root).as_posix(),
    )
    partial = archive_path.with_name(f".{archive_path.name}.partial")
    try:
        with ZipFile(partial, "w", ZIP_DEFLATED) as archive:
            for path in markdown_files:
                relative = path.relative_to(output_root).as_posix()
                if not relative.endswith(".md"):
                    raise ValueError("Result archive members must be Markdown files.")
                archive.write(path, relative)
        partial.replace(archive_path)
        return archive_path
    finally:
        partial.unlink(missing_ok=True)


def _inline(value: str) -> str:
    return value.replace("`", "'").replace("\n", " ").replace("\r", " ")[:300]


def _cell(value: str) -> str:
    first_line = value.splitlines()[0] if value.splitlines() else ""
    without_paths = re.sub(r"/(?:private/)?tmp/[^\s|]+", "<local path>", first_line)
    return without_paths.replace("|", "\\|").strip()[:300]
```

- [ ] **Step 4: Run result and full verification**

Run:

```bash
.venv/bin/pytest tests/test_results.py -v
.venv/bin/pytest -q
.venv/bin/ruff check app tests
```

Expected: report sanitization and Markdown-only packaging tests pass; full suite and Ruff pass.

- [ ] **Step 5: Commit result construction**

```bash
git add app/results.py tests/test_results.py
git commit -m "feat: package Markdown-only results"
```

## Task 6: Orchestrate jobs and clean temporary data

**Files:**

- Create: `app/processing.py`
- Create: `app/cleanup.py`
- Create: `tests/test_processing.py`
- Create: `tests/test_cleanup.py`

- [ ] **Step 1: Write a failing mixed-archive pipeline test**

Use a real small ZIP, the real extractor, and this fake converter:

```python
from dataclasses import replace
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.conversion import ConversionResult
from app.jobs import JobStore
from app.processing import JobProcessor
from app.settings import Settings


class FakeConverter:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()

    def convert(self, source: Path, output: Path) -> ConversionResult:
        if source.name in self.fail:
            return ConversionResult("failed", "Unsupported test file.")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return ConversionResult("converted")


def write_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return path


def settings(tmp_path: Path) -> Settings:
    return Settings(work_root=tmp_path / "jobs")


def test_pipeline_keeps_successes_and_reports_failures(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "mixed.zip", {
        "good.txt": b"good",
        "bad.bin": b"bad",
    })
    store = JobStore(tmp_path / "jobs")
    job = store.create("mixed.zip")
    archive.replace(job.root / "upload.zip")
    processor = JobProcessor(settings(tmp_path), store, FakeConverter(fail={"bad.bin"}))
    processor.process(job.id, job.root / "upload.zip")
    snapshot = store.snapshot(job.id)
    assert snapshot["status"] == "complete"
    assert snapshot["converted"] == 1
    assert snapshot["failed"] == 1
    with ZipFile(store.get(job.id).result_path) as result:
        assert result.namelist() == ["conversion-report.md", "good.txt.md"]


def test_pipeline_stops_on_global_archive_limit(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "large.zip", {"large.txt": b"too large"})
    limited = replace(settings(tmp_path), max_extracted_bytes=2)
    store = JobStore(limited.work_root)
    job = store.create("large.zip")
    archive.replace(job.root / "upload.zip")
    JobProcessor(limited, store, FakeConverter()).process(job.id, job.root / "upload.zip")
    snapshot = store.snapshot(job.id)
    assert snapshot["status"] == "failed"
    assert snapshot["download_available"] is False
```

- [ ] **Step 2: Write failing cleanup tests**

Create `tests/test_cleanup.py`:

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.cleanup import cleanup_expired_jobs, clear_startup_workspace
from app.jobs import JobStore
from app.models import JobStatus


def test_startup_cleanup_removes_children_and_preserves_root(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    (root / "old" / "nested").mkdir(parents=True)
    (root / "old" / "nested" / "private.txt").write_text("private", encoding="utf-8")
    clear_startup_workspace(root)
    assert root.is_dir()
    assert list(root.iterdir()) == []


def test_expiry_cleanup_deletes_only_expired_terminal_jobs(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    store = JobStore(tmp_path / "jobs", clock=lambda: now)
    expired = store.create("expired.zip")
    current = store.create("current.zip")
    store.set_status(expired.id, JobStatus.COMPLETE)
    store.set_status(current.id, JobStatus.COMPLETE)
    store.set_last_access(expired.id, now - timedelta(seconds=3_601))
    assert cleanup_expired_jobs(store, 3_600) == 1
    assert not expired.root.exists()
    assert current.root.exists()
```

- [ ] **Step 3: Run processing tests and confirm RED**

Run: `.venv/bin/pytest tests/test_processing.py tests/test_cleanup.py -v`

Expected: collection fails because `app.processing` and `app.cleanup` do not exist.

- [ ] **Step 4: Implement the single-worker processor**

Create `app/processing.py`:

```python
import re
import shutil
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from pathlib import Path

from app.archives import ArchiveError, ArchiveExtractor
from app.conversion import LocalConverter
from app.jobs import JobStore
from app.models import ConversionIssue, JobStatus
from app.results import build_result_archive, output_relative_path, write_conversion_report
from app.settings import Settings


class JobProcessor:
    def __init__(
        self,
        settings: Settings,
        store: JobStore,
        converter: LocalConverter,
        executor: Executor | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.converter = converter
        self.executor = executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="conversion",
        )

    def submit(self, job_id: str, archive_path: Path) -> Future[None]:
        self.store.set_status(job_id, JobStatus.QUEUED)
        return self.executor.submit(self.process, job_id, archive_path)

    def process(self, job_id: str, archive_path: Path) -> None:
        job = self.store.get(job_id, touch=False)
        input_root = job.root / "input"
        output_root = job.root / "output"
        output_root.mkdir(parents=True, exist_ok=True)
        try:
            self.store.start_stage(job_id, JobStatus.EXTRACTING)
            extraction = ArchiveExtractor(self.settings).extract(archive_path, input_root)
            self.store.start_stage(
                job_id,
                JobStatus.CONVERTING,
                total=len(extraction.files) + len(extraction.issues),
            )
            for issue in extraction.issues:
                self.store.record_issue(job_id, issue)
            for source in extraction.files:
                relative = source.relative_to(input_root)
                relative_name = relative.as_posix()
                self.store.set_current_file(job_id, relative_name)
                result = self.converter.convert(source, output_root / output_relative_path(relative))
                if result.outcome == "converted":
                    self.store.record_converted(job_id, relative_name, empty=result.empty)
                else:
                    self.store.record_issue(
                        job_id,
                        ConversionIssue(relative_name, "failed", result.reason or "Conversion failed."),
                    )
            self.store.start_stage(job_id, JobStatus.PACKAGING)
            current = self.store.get(job_id, touch=False)
            write_conversion_report(
                output_root,
                current.original_name,
                current.issues,
                current.converted,
            )
            safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(current.original_name).stem)
            result_path = current.root / f"{safe_stem or 'archive'}-markdown.zip"
            build_result_archive(output_root, result_path)
            self.store.set_result(job_id, result_path)
            shutil.rmtree(input_root, ignore_errors=True)
            archive_path.unlink(missing_ok=True)
        except ArchiveError as error:
            self.store.set_status(job_id, JobStatus.FAILED, error=self._safe_error(error, job.root))
        except Exception as error:
            self.store.set_status(job_id, JobStatus.FAILED, error=self._safe_error(error, job.root))

    @staticmethod
    def _safe_error(error: Exception, job_root: Path) -> str:
        reason = f"{type(error).__name__}: {error}"
        return reason.replace(str(job_root), "<job>").replace("\n", " ").replace("\r", " ")[:500]
```

- [ ] **Step 5: Implement startup and expiry cleanup**

Create `app/cleanup.py`:

```python
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


async def cleanup_loop(store: JobStore, ttl_seconds: int, interval_seconds: int = 60) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        cleanup_expired_jobs(store, ttl_seconds)
```

- [ ] **Step 6: Run processing, cleanup, and full suites**

Run:

```bash
.venv/bin/pytest tests/test_processing.py tests/test_cleanup.py -v
.venv/bin/pytest -q
.venv/bin/ruff check app tests
```

Expected: mixed conversion, global failure, startup cleanup, and expiry tests pass; full suite is green.

- [ ] **Step 7: Commit orchestration and cleanup**

```bash
git add app/processing.py app/cleanup.py tests/test_processing.py tests/test_cleanup.py
git commit -m "feat: orchestrate conversion jobs and cleanup"
```

## Task 7: Expose the loopback FastAPI application

**Files:**

- Create: `app/main.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: Write failing API tests for upload and lifecycle**

Create `tests/test_api.py` with an inline executor and fake converter so API tests complete synchronously:

```python
import io
from collections.abc import Callable
from concurrent.futures import Executor, Future
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient

from app.conversion import ConversionResult, LocalConverter
from app.jobs import JobStore
from app.main import create_app
from app.processing import JobProcessor
from app.settings import Settings


class InlineExecutor(Executor):
    def submit(self, fn: Callable, /, *args: object, **kwargs: object) -> Future:
        future: Future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as error:
            future.set_exception(error)
        return future


class TextConverter(LocalConverter):
    def __init__(self) -> None:
        pass

    def convert(self, source: Path, output: Path) -> ConversionResult:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return ConversionResult("converted")


def valid_zip_bytes() -> bytes:
    stream = io.BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr("notes.txt", "hello")
    return stream.getvalue()


def zip_names(content: bytes) -> list[str]:
    with ZipFile(io.BytesIO(content)) as archive:
        return archive.namelist()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(work_root=tmp_path / "jobs")

    def processor_factory(settings: Settings, store: JobStore) -> JobProcessor:
        return JobProcessor(settings, store, TextConverter(), InlineExecutor())

    with TestClient(create_app(settings, processor_factory)) as test_client:
        yield test_client


def test_valid_upload_returns_downloadable_markdown_zip(client: TestClient) -> None:
    response = client.post(
        "/api/jobs",
        files={"archive": ("documents.zip", valid_zip_bytes(), "application/zip")},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "complete"
    download = client.get(f"/api/jobs/{job_id}/download")
    assert download.status_code == 200
    assert all(name.endswith(".md") for name in zip_names(download.content))


@pytest.mark.parametrize("name,content,status", [
    ("notes.txt", b"not zip", 400),
    ("fake.zip", b"not zip", 400),
])
def test_invalid_uploads_are_rejected(name: str, content: bytes, status: int, client: TestClient) -> None:
    response = client.post("/api/jobs", files={"archive": (name, content)})
    assert response.status_code == status


def test_missing_job_is_not_found(client: TestClient) -> None:
    assert client.get("/api/jobs/missing").status_code == 404


def test_active_job_cannot_be_deleted(client: TestClient) -> None:
    job = client.app.state.jobs.create("active.zip")
    assert client.delete(f"/api/jobs/{job.id}").status_code == 409


def test_health_is_local_readiness_contract(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
```

Add the explicit size-limit and deletion tests:

```python
def test_upload_limit_is_enforced(tmp_path: Path) -> None:
    limited = Settings(work_root=tmp_path / "limited-jobs", max_upload_bytes=8)

    def processor_factory(settings: Settings, store: JobStore) -> JobProcessor:
        return JobProcessor(settings, store, TextConverter(), InlineExecutor())

    with TestClient(create_app(limited, processor_factory)) as test_client:
        response = test_client.post(
            "/api/jobs",
            files={"archive": ("large.zip", valid_zip_bytes(), "application/zip")},
        )
    assert response.status_code == 413


def test_completed_job_can_be_deleted(client: TestClient) -> None:
    response = client.post(
        "/api/jobs",
        files={"archive": ("documents.zip", valid_zip_bytes(), "application/zip")},
    )
    job_id = response.json()["job_id"]
    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
```

- [ ] **Step 2: Run API tests and confirm RED**

Run: `.venv/bin/pytest tests/test_api.py -v`

Expected: collection fails because `app.main` does not exist.

- [ ] **Step 3: Implement application wiring and lifespan**

Create `app/main.py`:

```python
import asyncio
import contextlib
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from zipfile import is_zipfile

from fastapi import FastAPI, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

from app.cleanup import cleanup_loop, clear_startup_workspace
from app.conversion import LocalConverter
from app.jobs import JobBusy, JobNotFound, JobStore
from app.processing import JobProcessor
from app.settings import Settings

ProcessorFactory = Callable[[Settings, JobStore], JobProcessor]


def _default_processor(settings: Settings, store: JobStore) -> JobProcessor:
    return JobProcessor(settings, store, LocalConverter(settings.conversion_timeout_seconds))


def create_app(
    settings: Settings | None = None,
    processor_factory: ProcessorFactory | None = None,
) -> FastAPI:
    configured = settings or Settings()
    factory = processor_factory or _default_processor

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        clear_startup_workspace(configured.work_root)
        store = JobStore(configured.work_root)
        processor = factory(configured, store)
        application.state.settings = configured
        application.state.jobs = store
        application.state.processor = processor
        cleanup_task = asyncio.create_task(
            cleanup_loop(store, configured.job_ttl_seconds),
            name="job-cleanup",
        )
        try:
            yield
        finally:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task
            processor.executor.shutdown(wait=False, cancel_futures=True)

    application = FastAPI(title="Zip to Markdown", lifespan=lifespan)

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/api/jobs", status_code=202)
    async def create_job(archive: UploadFile) -> dict[str, str]:
        filename = Path(archive.filename or "").name
        if not filename or Path(filename).suffix.lower() != ".zip":
            raise HTTPException(400, "Choose one ZIP archive.")
        store: JobStore = application.state.jobs
        job = store.create(filename)
        upload_path = job.root / "upload.zip"
        uploaded = 0
        try:
            with upload_path.open("xb") as output:
                while chunk := await archive.read(configured.chunk_size):
                    uploaded += len(chunk)
                    if uploaded > configured.max_upload_bytes:
                        raise HTTPException(413, "The ZIP exceeds the 1 GiB upload limit.")
                    output.write(chunk)
            if not is_zipfile(upload_path):
                raise HTTPException(400, "The uploaded file is not a valid ZIP archive.")
        except HTTPException:
            store.delete(job.id, allow_active=True)
            raise
        finally:
            await archive.close()
        application.state.processor.submit(job.id, upload_path)
        return {"job_id": job.id}

    @application.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, object]:
        try:
            return application.state.jobs.snapshot(job_id)
        except JobNotFound as error:
            raise HTTPException(404, "Conversion job was not found.") from error

    @application.get("/api/jobs/{job_id}/download")
    async def download_job(job_id: str) -> FileResponse:
        try:
            job = application.state.jobs.get(job_id)
        except JobNotFound as error:
            raise HTTPException(404, "Conversion job was not found.") from error
        if job.result_path is None or not job.result_path.is_file():
            raise HTTPException(409, "The result is not ready.")
        return FileResponse(
            job.result_path,
            media_type="application/zip",
            filename=job.result_path.name,
        )

    @application.delete("/api/jobs/{job_id}", status_code=204)
    async def delete_job(job_id: str) -> Response:
        try:
            application.state.jobs.delete(job_id)
        except JobNotFound as error:
            raise HTTPException(404, "Conversion job was not found.") from error
        except JobBusy as error:
            raise HTTPException(409, "Wait for conversion to finish before deleting files.") from error
        return Response(status_code=204)

    return application


app = create_app()
```

The UI task will mount static files, configure templates, and add `GET /`; keeping Task 7 API-only allows its tests to run before UI files exist.

- [ ] **Step 4: Run API and full suites**

Run:

```bash
.venv/bin/pytest tests/test_api.py -v
.venv/bin/pytest -q
.venv/bin/ruff check app tests
```

Expected: upload, limit, invalid ZIP, download, delete, missing, busy, and health tests pass.

- [ ] **Step 5: Commit the local API**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: expose local conversion API"
```

## Task 8: Build the approved accessible browser interface

**Files:**

- Create: `app/templates/index.html`
- Create: `app/static/app.css`
- Create: `app/static/app.js`
- Create: `tests/test_browser.py`

- [ ] **Step 1: Write failing page-contract tests**

Before browser automation, add a fast contract test through TestClient:

```python
def test_home_page_contains_accessible_workflow(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="archive-input"' in response.text
    assert 'aria-live="polite"' in response.text
    assert "Files never leave this Mac" in response.text
    assert 'id="download-button"' in response.text
    assert 'id="delete-button"' in response.text
```

Run: `.venv/bin/pytest tests/test_browser.py -v`

Expected: `GET /` returns `404` or template configuration fails because the UI files do not exist.

- [ ] **Step 2: Create the semantic page structure**

Create `index.html` with a top bar (“Zip to Markdown”, “Powered by Microsoft MarkItDown”, “100% local”), a heading (“Turn a ZIP into clean Markdown”), a `<label>`-backed hidden file input with `accept=".zip,application/zip"`, the approved drop zone, and four sections identified as `ready-panel`, `upload-panel`, `conversion-panel`, and `complete-panel`. Include `role="status" aria-live="polite"` for state text, `<progress>` elements for upload and conversion, converted/skipped/failed counters, a conditional `empty-count` note for successful files with no extracted text, a download anchor, delete button, inline error region, and a `<noscript>` warning. Load only `/static/app.css` and `/static/app.js`.

Add `Request`, `HTMLResponse`, `StaticFiles`, and `Jinja2Templates` imports to `app/main.py`, then insert this wiring inside `create_app()` after `application` is created and before it is returned:

```python
templates = Jinja2Templates(directory="app/templates")
application.mount("/static", StaticFiles(directory="app/static"), name="static")


@application.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")
```

- [ ] **Step 3: Implement the approved visual system**

Create CSS using system fonts and these tokens:

```css
:root {
  color-scheme: light;
  --background: #f7f8fa;
  --surface: #ffffff;
  --text: #111827;
  --muted: #6b7280;
  --border: #dfe3e8;
  --primary: #2563eb;
  --primary-dark: #1d4ed8;
  --success-bg: #f0fdf4;
  --success-text: #166534;
  --danger: #b42318;
  --radius: 18px;
}
```

Match the approved centered 720px layout, dashed drop zone, blue-to-violet progress fill, three statistic cards, clear focus rings, 44px minimum interactive targets, and responsive behavior below 600px. Add `@media (prefers-reduced-motion: reduce)` to disable transitions and animation.

- [ ] **Step 4: Implement upload, polling, download, and deletion behavior**

Create `app.js` as an ES module-free script. Keep one `activeJobId`, prevent multiple simultaneous uploads, validate one `.zip` file up to 1 GiB, and use `XMLHttpRequest` so `xhr.upload.onprogress` updates the upload `<progress>`. After the `202` response, poll `GET /api/jobs/{id}` every 750ms. Render current filename, percentage, converted/empty/skipped/failed counts, and hide `empty-count` when zero. On `complete`, set the download link to `/api/jobs/{id}/download`; on `failed`, show the job error; on delete, call `DELETE`, clear the job ID, and return to ready state. Escape dynamic content through `textContent`, never `innerHTML`. Retry polling network failures with capped two-second delays without starting a new job.

- [ ] **Step 5: Add browser automation for the complete workflow**

Use Playwright request interception to fulfill the upload with `202`, return progress snapshots on status polls, and fulfill deletion with `204`. Assert:

```python
page.set_input_files("#archive-input", zip_fixture)
expect(page.locator("#conversion-panel")).to_be_visible()
expect(page.locator("#converted-count")).to_have_text("2")
expect(page.locator("#download-button")).to_have_attribute("href", re.compile("/download$"))
page.get_by_role("button", name="Delete files").click()
expect(page.locator("#ready-panel")).to_be_visible()
```

Add a 390×844 viewport assertion that no element produces horizontal overflow and a keyboard test that focuses and activates “Choose ZIP”.

- [ ] **Step 6: Run UI checks**

Run:

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/pytest tests/test_browser.py -v
.venv/bin/pytest -q
.venv/bin/ruff check app tests
```

Expected: page contract, complete flow, narrow viewport, and keyboard tests pass; full suite remains green.

- [ ] **Step 7: Commit the browser experience**

```bash
git add app/main.py app/templates/index.html app/static/app.css app/static/app.js tests/test_browser.py
git commit -m "feat: add local drag-and-drop interface"
```

## Task 9: Add the double-click Mac launcher and public documentation

**Files:**

- Create: `Start App.command`
- Create: `README.md`
- Create: `LICENSE`
- Create: `tests/test_launcher.py`

- [ ] **Step 1: Write failing launcher contract tests**

Create `tests/test_launcher.py`:

```python
def test_launcher_is_local_and_uses_runtime_lock() -> None:
    script = Path("Start App.command").read_text(encoding="utf-8")
    assert "requirements.lock" in script
    assert "--host 127.0.0.1" in script
    assert "/health" in script
    assert "open \"http://127.0.0.1:$PORT\"" in script
    assert "0.0.0.0" not in script
```

Run: `.venv/bin/pytest tests/test_launcher.py -v`

Expected: `FileNotFoundError` for `Start App.command`.

- [ ] **Step 2: Implement the launcher**

Create `Start App.command` with this executable Zsh implementation:

```zsh
#!/bin/zsh
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  print "Python 3.10 or newer is required. Install it from https://www.python.org/downloads/macos/"
  read -r "?Press Return to close."
  exit 1
fi

PYTHON_BIN="$(command -v python3)"
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  print "Your Python is too old. Install Python 3.10 or newer from python.org."
  read -r "?Press Return to close."
  exit 1
fi

VENV="$ROOT/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  print "Preparing Zip to Markdown for first use…"
  "$PYTHON_BIN" -m venv "$VENV"
fi

FINGERPRINT="$(/usr/bin/shasum -a 256 requirements.lock | /usr/bin/awk '{print $1}')"
INSTALLED_FINGERPRINT="$(/bin/cat "$VENV/.requirements.sha256" 2>/dev/null || true)"
if [[ "$FINGERPRINT" != "$INSTALLED_FINGERPRINT" ]]; then
  print "Installing local conversion components…"
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install -r requirements.lock
  print -r -- "$FINGERPRINT" > "$VENV/.requirements.sha256"
fi

PORT="$("$VENV/bin/python" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

"$VENV/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" &
SERVER_PID=$!

READY=0
for attempt in {1..60}; do
  if /usr/bin/curl --silent --fail "http://127.0.0.1:$PORT/health" >/dev/null; then
    READY=1
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    break
  fi
  sleep 0.5
done

if [[ "$READY" -ne 1 ]]; then
  print "Zip to Markdown could not start. Review the messages above."
  exit 1
fi

print "Zip to Markdown is running. Close this window or press Control-C to stop it."
open "http://127.0.0.1:$PORT"
wait "$SERVER_PID"
```

Run: `chmod +x "Start App.command"`.

- [ ] **Step 3: Add README and MIT license**

Write `README.md` with: “Quick start” using clone/download plus double-click; first-run network note; local-only privacy model; 1 GiB upload, 5 GiB extraction, 10,000-entry, depth-three, 15-minute-per-file, and one-hour expiry limits; output naming example; failure-report behavior; supported formats delegated to pinned MarkItDown; Python 3.10+ requirement; terminal fallback command; tests and lint commands; and troubleshooting for macOS Gatekeeper using Control-click → Open.

Create an MIT license with copyright `2026 Charnpreet Singh`.

- [ ] **Step 4: Verify launcher syntax and documentation contracts**

Run:

```bash
/bin/zsh -n "Start App.command"
test -x "Start App.command"
.venv/bin/pytest tests/test_launcher.py -v
.venv/bin/ruff check app tests
```

Expected: Zsh syntax exits `0`; launcher is executable; contract test and Ruff pass.

- [ ] **Step 5: Commit launcher and documentation**

```bash
git add "Start App.command" README.md LICENSE tests/test_launcher.py
git commit -m "docs: add Mac launcher and usage guide"
```

## Task 10: Verify real formats, security, UI, and fresh installation

**Files:**

- Create: `tests/fixtures/README.md`
- Create: `tests/test_markitdown_formats.py`
- Modify only if verification exposes a defect: relevant implementation and regression test files

- [ ] **Step 1: Write parameterized real-format smoke tests**

Generate minimal fixtures at test runtime for TXT, HTML, CSV, JSON, XML, DOCX, XLSX, PPTX, and PDF using the already locked libraries. Parameterize source suffix and a unique expected phrase. Call the real `LocalConverter` and assert outcome `converted`, output suffix `.md`, UTF-8 readability, and expected phrase presence. Keep generated fixtures under pytest's temporary directory; `tests/fixtures/README.md` explains why binary fixtures are generated rather than committed.

- [ ] **Step 2: Run the real MarkItDown matrix and fix only evidenced defects**

Run: `.venv/bin/pytest tests/test_markitdown_formats.py -v`

Expected: all nine representative local formats convert. If a format exposes a pinned-library limitation, record the exact result in README and keep the app behavior as a reported per-file failure rather than adding cloud services.

- [ ] **Step 3: Run the complete automated verification suite**

Run:

```bash
.venv/bin/pytest -v
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
/bin/zsh -n "Start App.command"
git diff --check
```

Expected: zero failed tests, zero lint or format issues, valid Zsh syntax, and no whitespace errors.

- [ ] **Step 4: Perform an adversarial archive verification**

Run the focused security suite with output visible:

```bash
.venv/bin/pytest tests/test_archives.py -vv
```

Expected: traversal, Windows path, symbolic link, non-regular entry, extraction-byte, entry-count, path-length, and nested-depth cases all pass without writing outside pytest temporary directories.

- [ ] **Step 5: Verify a clean launcher environment**

Move the existing environment aside, launch the script, wait for the browser health response, convert a mixed sample ZIP, download the result, inspect it with `unzip -l`, then restore or remove the old environment only after success:

```bash
mv .venv .venv-development
./Start\ App.command
```

Expected: the launcher creates a new environment, opens the local page, and the downloaded archive lists only `.md` members. Stop with Control-C. Remove the fresh `.venv` and rename `.venv-development` back to `.venv`.

- [ ] **Step 6: Check the implementation against every acceptance criterion**

Read `docs/superpowers/specs/2026-07-15-zip-to-markdown-design.md` and record any gap as a failing automated test before changing production code. Re-run Step 3 after any correction.

- [ ] **Step 7: Commit verification fixtures and any evidenced corrections**

```bash
git add tests/fixtures/README.md tests/test_markitdown_formats.py app tests README.md
git commit -m "test: verify local conversion workflow"
```

## Task 11: Prepare the branch for optional GitHub publication

**Files:**

- Modify only if checks find an issue: files already listed above

- [ ] **Step 1: Inspect exact branch scope**

Run:

```bash
git status --short --branch
git log --oneline --decorate --graph main..HEAD
git diff --stat main...HEAD
```

Expected: only Zip to Markdown application, tests, documentation, and plan files are present; the working tree is clean.

- [ ] **Step 2: Run final fresh evidence checks**

Run:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
/bin/zsh -n "Start App.command"
git diff --check main...HEAD
```

Expected: all commands exit `0` immediately before completion is reported.

- [ ] **Step 3: Stop before external publication**

Report the local repository path, branch name, commit list, test count, and exact verification commands. Ask for confirmation before creating a GitHub repository or pushing because publication is an external state change and was described as optional.
