import contextlib
import re
import shutil
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

from app.archives import ArchiveError, ArchiveExtractor
from app.conversion import ConversionResult, LocalConverter
from app.jobs import JobStore
from app.models import ConversionIssue, JobStatus
from app.results import build_result_archive, output_relative_path, write_conversion_report
from app.settings import Settings


class Converter(Protocol):
    def convert(self, source: Path, output: Path) -> ConversionResult:
        pass


class JobProcessor:
    def __init__(
        self,
        settings: Settings,
        store: JobStore,
        converter: Converter | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.converter = converter or LocalConverter(settings.conversion_timeout_seconds)
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
        try:
            output_root.mkdir(parents=True, exist_ok=True)
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
                result = self.converter.convert(
                    source,
                    output_root / output_relative_path(relative),
                )
                if result.outcome == "converted":
                    self.store.record_converted(job_id, relative_name, empty=result.empty)
                else:
                    self.store.record_issue(
                        job_id,
                        ConversionIssue(
                            relative_name,
                            "failed",
                            result.reason or "Conversion failed.",
                        ),
                    )

            self.store.start_stage(job_id, JobStatus.PACKAGING)
            current = self.store.get(job_id, touch=False)
            write_conversion_report(
                output_root,
                current.original_name,
                current.issues,
                current.converted,
            )
            safe_stem = re.sub(
                r"[^A-Za-z0-9._-]+",
                "-",
                Path(current.original_name).stem,
            ).strip(".-")
            result_path = current.root / f"{safe_stem or 'archive'}-markdown.zip"
            build_result_archive(output_root, result_path)
            self.store.set_result(job_id, result_path)

            shutil.rmtree(input_root, ignore_errors=True)
            with contextlib.suppress(OSError):
                archive_path.unlink(missing_ok=True)
        except ArchiveError as error:
            self.store.set_status(
                job_id,
                JobStatus.FAILED,
                error=self._safe_error(error, job.root),
            )
        except Exception as error:
            self.store.set_status(
                job_id,
                JobStatus.FAILED,
                error=self._safe_error(error, job.root),
            )

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _safe_error(error: Exception, job_root: Path) -> str:
        reason = f"{type(error).__name__}: {error}"
        return reason.replace(str(job_root), "<job>").replace("\n", " ").replace("\r", " ")[:500]
