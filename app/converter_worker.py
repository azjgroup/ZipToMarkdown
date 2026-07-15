import sys
from pathlib import Path

from markitdown import MarkItDown


def _normalize_markdown(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return f"{normalized.rstrip(chr(10))}\n" if normalized else ""


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
        output.write_text(
            _normalize_markdown(result.text_content or ""),
            encoding="utf-8",
        )
        return 0
    except Exception as error:
        error_path.write_text(_sanitize_error(error, source), encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
