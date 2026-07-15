import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class ConversionResult:
    outcome: str
    reason: str | None = None
    empty: bool = False


Runner = Callable[..., subprocess.CompletedProcess[str]]


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
                reason = (
                    error_path.read_text(encoding="utf-8")
                    if error_path.exists()
                    else completed.stderr
                )
                return ConversionResult("failed", self._sanitize_reason(reason, source))
            if not partial.exists():
                return ConversionResult("failed", "The converter produced no output file.")

            empty = self._is_empty(partial)
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
    def _is_empty(path: Path) -> bool:
        with path.open(encoding="utf-8") as markdown:
            while chunk := markdown.read(64 * 1024):
                if chunk.strip():
                    return False
        return True

    @staticmethod
    def _sanitize_reason(reason: str | None, source: Path) -> str:
        cleaned = (reason or "The converter exited without an explanation.").strip()
        cleaned = cleaned.replace(str(source), "<input>")
        cleaned = cleaned.replace("\n", " ").replace("\r", " ")
        return cleaned[:1_000]
