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
        self._root: Path | None = None

    def extract(self, archive_path: Path, destination: Path) -> ExtractionResult:
        if not is_zipfile(archive_path):
            raise ArchiveError("The uploaded file is not a valid ZIP archive.")
        destination.mkdir(parents=True, exist_ok=False)
        self._root = destination.resolve()
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
            raise ArchiveError("The nested ZIP archive is malformed.") from error

        with archive:
            for info in archive.infolist():
                counters.entries += 1
                if counters.entries > self.settings.max_entries:
                    raise ArchiveLimitError("The archive exceeds the entry limit.")

                try:
                    relative = self._safe_relative_path(info, destination)
                except ArchiveError as error:
                    issues.append(
                        ConversionIssue(
                            self._display_name(destination, info.filename),
                            "skipped",
                            str(error),
                        )
                    )
                    continue

                if self._is_metadata(relative) or info.is_dir():
                    continue

                mode = info.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                issue_path = self._display_name(destination, relative.as_posix())
                if stat.S_ISLNK(mode):
                    issues.append(
                        ConversionIssue(
                            issue_path,
                            "skipped",
                            "Symbolic links are not extracted.",
                        )
                    )
                    continue
                if file_type not in {0, stat.S_IFREG}:
                    issues.append(
                        ConversionIssue(
                            issue_path,
                            "skipped",
                            "Non-regular entries are not extracted.",
                        )
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
                                raise ArchiveLimitError(
                                    "The archive exceeds the extracted byte limit."
                                )
                except ArchiveLimitError:
                    target.unlink(missing_ok=True)
                    raise

                if target.suffix.lower() != ".zip":
                    files.append(target)
                    continue

                if depth >= self.settings.max_nested_depth:
                    issues.append(
                        ConversionIssue(
                            issue_path,
                            "skipped",
                            (
                                "Nested ZIP depth exceeds the limit of "
                                f"{self.settings.max_nested_depth}."
                            ),
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
                    issues.append(ConversionIssue(issue_path, "skipped", str(error)))
                finally:
                    target.unlink(missing_ok=True)

    def _safe_relative_path(self, info: ZipInfo, destination: Path) -> Path:
        name = info.filename.replace("\\", "/")
        if "\x00" in name:
            raise ArchiveError("NUL bytes are not allowed in archive paths.")

        pure_path = PurePosixPath(name)
        if (
            not pure_path.parts
            or pure_path == PurePosixPath(".")
            or pure_path.is_absolute()
            or PureWindowsPath(name).drive
            or ".." in pure_path.parts
        ):
            raise ArchiveError("Unsafe archive path was rejected.")
        if len(pure_path.as_posix()) > self.settings.max_path_length:
            raise ArchiveError("Archive path exceeds the path-length limit.")

        relative = Path(*pure_path.parts)
        resolved_destination = destination.resolve()
        candidate = (resolved_destination / relative).resolve()
        if not candidate.is_relative_to(resolved_destination):
            raise ArchiveError("Unsafe archive path was rejected.")
        return relative

    def _display_name(self, destination: Path, name: str) -> str:
        sanitized = name.replace("\n", " ").replace("\r", " ")[:1_024]
        if self._root is None:
            return sanitized
        prefix = destination.resolve().relative_to(self._root).as_posix()
        return f"{prefix}/{sanitized}" if prefix != "." else sanitized

    @staticmethod
    def _is_metadata(relative: Path) -> bool:
        return relative.name == ".DS_Store" or "__MACOSX" in relative.parts
