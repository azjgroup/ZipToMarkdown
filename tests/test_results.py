from pathlib import Path
from zipfile import ZipFile

from app.models import ConversionIssue
from app.results import (
    build_result_archive,
    output_relative_path,
    write_conversion_report,
)


def test_output_name_retains_source_extension() -> None:
    assert output_relative_path(Path("docs/report.pdf")) == Path("docs/report.pdf.md")
    assert output_relative_path(Path("docs/report.docx")) == Path("docs/report.docx.md")


def test_report_is_not_created_without_issues(tmp_path: Path) -> None:
    assert write_conversion_report(tmp_path, "input.zip", [], 2) is None
    assert not (tmp_path / "conversion-report.md").exists()


def test_report_sanitizes_paths_traces_and_markdown_tables(tmp_path: Path) -> None:
    report = write_conversion_report(
        tmp_path,
        "input.zip",
        [
            ConversionIssue(
                "bad|file.bin",
                "failed",
                "/private/tmp/job/input.bin secret\nTraceback: private detail",
            )
        ],
        1,
    )

    assert report is not None
    text = report.read_text(encoding="utf-8")
    assert "bad\\|file.bin" in text
    assert "/private/tmp" not in text
    assert "Traceback" not in text
    assert "Converted: 1" in text
    assert "Failed: 1" in text


def test_result_archive_contains_only_markdown_in_stable_order(tmp_path: Path) -> None:
    output = tmp_path / "output"
    (output / "docs").mkdir(parents=True)
    (output / "z-last.md").write_text("last", encoding="utf-8")
    (output / "docs" / "report.pdf.md").write_text("report", encoding="utf-8")
    (output / "accidental.bin").write_bytes(b"never package")

    archive = build_result_archive(output, tmp_path / "result.zip")

    with ZipFile(archive) as result:
        assert result.namelist() == ["docs/report.pdf.md", "z-last.md"]
        assert all(name.endswith(".md") for name in result.namelist())


def test_result_archive_can_contain_only_failure_report(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "conversion-report.md").write_text("failed", encoding="utf-8")

    archive = build_result_archive(output, tmp_path / "result.zip")

    with ZipFile(archive) as result:
        assert result.namelist() == ["conversion-report.md"]
