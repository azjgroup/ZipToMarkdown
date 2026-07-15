from dataclasses import replace
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.conversion import ConversionResult
from app.jobs import JobStore
from app.processing import JobProcessor
from app.settings import Settings


class FakeConverter:
    def __init__(
        self,
        fail: set[str] | None = None,
        empty: set[str] | None = None,
    ) -> None:
        self.fail = fail or set()
        self.empty = empty or set()

    def convert(self, source: Path, output: Path) -> ConversionResult:
        if source.name in self.fail:
            return ConversionResult("failed", "Unsupported test file.")
        output.parent.mkdir(parents=True, exist_ok=True)
        content = "" if source.name in self.empty else source.read_text(encoding="utf-8")
        output.write_text(content, encoding="utf-8")
        return ConversionResult("converted", empty=not content)


def write_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return path


def settings_for(tmp_path: Path) -> Settings:
    return Settings(work_root=tmp_path / "jobs")


def test_pipeline_keeps_successes_and_reports_failures(tmp_path: Path) -> None:
    archive = write_zip(
        tmp_path / "mixed.zip",
        {"good.txt": b"good", "bad.bin": b"bad"},
    )
    settings = settings_for(tmp_path)
    store = JobStore(settings.work_root)
    job = store.create("mixed.zip")
    upload = job.root / "upload.zip"
    archive.replace(upload)
    processor = JobProcessor(settings, store, FakeConverter(fail={"bad.bin"}))

    processor.process(job.id, upload)

    snapshot = store.snapshot(job.id)
    assert snapshot["status"] == "complete"
    assert snapshot["converted"] == 1
    assert snapshot["failed"] == 1
    result_path = store.get(job.id).result_path
    assert result_path is not None
    with ZipFile(result_path) as result:
        assert result.namelist() == ["conversion-report.md", "good.txt.md"]
    assert not upload.exists()
    assert not (job.root / "input").exists()


def test_pipeline_counts_successful_empty_output(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "empty.zip", {"empty.txt": b"source"})
    settings = settings_for(tmp_path)
    store = JobStore(settings.work_root)
    job = store.create("empty.zip")
    upload = job.root / "upload.zip"
    archive.replace(upload)

    JobProcessor(settings, store, FakeConverter(empty={"empty.txt"})).process(job.id, upload)

    snapshot = store.snapshot(job.id)
    assert snapshot["converted"] == 1
    assert snapshot["empty"] == 1


def test_pipeline_stops_on_global_archive_limit(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "large.zip", {"large.txt": b"too large"})
    limited = replace(settings_for(tmp_path), max_extracted_bytes=2)
    store = JobStore(limited.work_root)
    job = store.create("large.zip")
    upload = job.root / "upload.zip"
    archive.replace(upload)

    JobProcessor(limited, store, FakeConverter()).process(job.id, upload)

    snapshot = store.snapshot(job.id)
    assert snapshot["status"] == "failed"
    assert snapshot["download_available"] is False
    assert "extracted byte limit" in str(snapshot["error"])


def test_pipeline_sanitizes_job_paths_from_unexpected_errors(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "error.zip", {"good.txt": b"good"})
    settings = settings_for(tmp_path)
    store = JobStore(settings.work_root)
    job = store.create("error.zip")
    upload = job.root / "upload.zip"
    archive.replace(upload)

    class RaisingConverter:
        def convert(self, source: Path, output: Path) -> ConversionResult:
            raise OSError(f"failed inside {job.root}")

    JobProcessor(settings, store, RaisingConverter()).process(job.id, upload)

    snapshot = store.snapshot(job.id)
    assert snapshot["status"] == "failed"
    assert str(job.root) not in str(snapshot["error"])
    assert "<job>" in str(snapshot["error"])
