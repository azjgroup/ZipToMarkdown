import subprocess
from pathlib import Path
from subprocess import CompletedProcess

from app.conversion import LocalConverter
from app.converter_worker import main as worker_main


def test_converter_promotes_worker_output(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    output = tmp_path / "out" / "input.txt.md"
    source.write_text("hello", encoding="utf-8")

    def successful_runner(command: list[str], **_: object) -> CompletedProcess[str]:
        Path(command[-2]).write_text("# Hello\n", encoding="utf-8")
        return CompletedProcess(command, 0, "", "")

    result = LocalConverter(timeout_seconds=1, runner=successful_runner).convert(
        source,
        output,
    )

    assert result.outcome == "converted"
    assert result.empty is False
    assert output.read_text(encoding="utf-8") == "# Hello\n"


def test_converter_marks_empty_worker_output(tmp_path: Path) -> None:
    source = tmp_path / "empty.txt"
    output = tmp_path / "out" / "empty.txt.md"
    source.write_text("empty", encoding="utf-8")

    def empty_runner(command: list[str], **_: object) -> CompletedProcess[str]:
        Path(command[-2]).write_text("", encoding="utf-8")
        return CompletedProcess(command, 0, "", "")

    result = LocalConverter(timeout_seconds=1, runner=empty_runner).convert(source, output)

    assert result.outcome == "converted"
    assert result.empty is True
    assert output.exists()


def test_converter_returns_sanitized_failure(tmp_path: Path) -> None:
    source = tmp_path / "input.bin"
    output = tmp_path / "out" / "input.bin.md"
    source.write_bytes(b"binary")

    def failing_runner(command: list[str], **_: object) -> CompletedProcess[str]:
        Path(command[-1]).write_text(
            f"UnsupportedFormat: cannot convert {source}",
            encoding="utf-8",
        )
        return CompletedProcess(command, 1, "", "")

    result = LocalConverter(timeout_seconds=1, runner=failing_runner).convert(source, output)

    assert result.outcome == "failed"
    assert result.reason == "UnsupportedFormat: cannot convert <input>"
    assert not output.exists()


def test_converter_reports_timeout(tmp_path: Path) -> None:
    source = tmp_path / "slow.txt"
    output = tmp_path / "out" / "slow.txt.md"
    source.write_text("slow", encoding="utf-8")

    def timeout_runner(*_: object, **__: object) -> CompletedProcess[str]:
        raise subprocess.TimeoutExpired("worker", 1)

    result = LocalConverter(timeout_seconds=1, runner=timeout_runner).convert(source, output)

    assert result.outcome == "failed"
    assert result.reason == "Conversion exceeded the 1-second time limit."


def test_converter_rejects_success_without_output(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    output = tmp_path / "out" / "input.txt.md"
    source.write_text("text", encoding="utf-8")

    def no_output_runner(command: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(command, 0, "", "")

    result = LocalConverter(timeout_seconds=1, runner=no_output_runner).convert(source, output)

    assert result.outcome == "failed"
    assert result.reason == "The converter produced no output file."


def test_real_worker_converts_local_text_file(tmp_path: Path) -> None:
    source = tmp_path / "local.txt"
    output = tmp_path / "local.md"
    error = tmp_path / "error.txt"
    source.write_text("Local conversion phrase", encoding="utf-8")

    exit_code = worker_main([str(source), str(output), str(error)])

    assert exit_code == 0
    assert "Local conversion phrase" in output.read_text(encoding="utf-8")
    assert not error.exists()
