import re
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.models import ConversionIssue

ABSOLUTE_PATH = re.compile(r"/(?:[^/\s|]+/)+[^/\s|]+")


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

    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / "conversion-report.md"
    skipped_count = sum(issue.outcome == "skipped" for issue in issues)
    failed_count = len(issues) - skipped_count
    lines = [
        "# Conversion report",
        "",
        f"- Archive: `{_inline(original_name)}`",
        f"- Completed: {datetime.now(UTC).isoformat()}",
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
    archive_path.parent.mkdir(parents=True, exist_ok=True)
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
    without_paths = ABSOLUTE_PATH.sub("<local path>", first_line)
    return without_paths.replace("|", "\\|").strip()[:300]
