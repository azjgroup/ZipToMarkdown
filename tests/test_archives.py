import io
import stat
from dataclasses import replace
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from app.archives import ArchiveError, ArchiveExtractor, ArchiveLimitError
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


def settings_for(tmp_path: Path) -> Settings:
    return Settings(work_root=tmp_path / "jobs")


def test_extracts_regular_and_nested_files(tmp_path: Path) -> None:
    nested = make_zip_bytes({"notes.txt": b"nested"})
    archive = write_zip(
        tmp_path / "input.zip",
        {
            "docs/report.pdf": b"pdf",
            "bundle.zip": nested,
            "__MACOSX/._report": b"metadata",
            ".DS_Store": b"metadata",
        },
    )

    result = ArchiveExtractor(settings_for(tmp_path)).extract(archive, tmp_path / "out")

    assert [path.relative_to(tmp_path / "out").as_posix() for path in result.files] == [
        "bundle.zip.contents/notes.txt",
        "docs/report.pdf",
    ]
    assert result.issues == []


@pytest.mark.parametrize("unsafe_name", ["../escape.txt", "/absolute.txt", "C:/drive.txt"])
def test_rejects_unsafe_paths_without_stopping_safe_entries(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    archive = write_zip(
        tmp_path / "paths.zip",
        {unsafe_name: b"escape", "safe.txt": b"safe"},
    )

    result = ArchiveExtractor(settings_for(tmp_path)).extract(archive, tmp_path / "out")

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

    result = ArchiveExtractor(settings_for(tmp_path)).extract(archive, tmp_path / "out")

    assert result.files == []
    assert result.issues[0].reason == "Symbolic links are not extracted."


def test_rejects_non_regular_entries(tmp_path: Path) -> None:
    archive = tmp_path / "fifo.zip"
    info = ZipInfo("pipe")
    info.create_system = 3
    info.external_attr = (stat.S_IFIFO | 0o644) << 16
    with ZipFile(archive, "w") as zipped:
        zipped.writestr(info, "data")

    result = ArchiveExtractor(settings_for(tmp_path)).extract(archive, tmp_path / "out")

    assert result.files == []
    assert result.issues[0].reason == "Non-regular entries are not extracted."


def test_enforces_actual_extracted_byte_limit(tmp_path: Path) -> None:
    settings = replace(settings_for(tmp_path), max_extracted_bytes=4)
    archive = write_zip(tmp_path / "large.zip", {"five.txt": b"12345"})

    with pytest.raises(ArchiveLimitError, match="extracted byte limit"):
        ArchiveExtractor(settings).extract(archive, tmp_path / "out")


def test_enforces_global_entry_limit(tmp_path: Path) -> None:
    settings = replace(settings_for(tmp_path), max_entries=1)
    archive = write_zip(tmp_path / "entries.zip", {"one": b"1", "two": b"2"})

    with pytest.raises(ArchiveLimitError, match="entry limit"):
        ArchiveExtractor(settings).extract(archive, tmp_path / "out")


def test_enforces_path_length_limit(tmp_path: Path) -> None:
    settings = replace(settings_for(tmp_path), max_path_length=8)
    archive = write_zip(tmp_path / "paths.zip", {"too-long.txt": b"data"})

    result = ArchiveExtractor(settings).extract(archive, tmp_path / "out")

    assert result.files == []
    assert result.issues[0].reason == "Archive path exceeds the path-length limit."


def test_skips_zip_beyond_nested_depth(tmp_path: Path) -> None:
    payload = make_zip_bytes({"deep.txt": b"deep"})
    for level in range(4, 0, -1):
        payload = make_zip_bytes({f"level-{level}.zip": payload})
    archive = tmp_path / "deep.zip"
    archive.write_bytes(payload)

    result = ArchiveExtractor(settings_for(tmp_path)).extract(archive, tmp_path / "out")

    assert result.files == []
    assert result.issues[-1].reason == "Nested ZIP depth exceeds the limit of 3."


def test_rejects_invalid_outer_zip(tmp_path: Path) -> None:
    archive = tmp_path / "invalid.zip"
    archive.write_bytes(b"not a zip")

    with pytest.raises(ArchiveError, match="not a valid ZIP"):
        ArchiveExtractor(settings_for(tmp_path)).extract(archive, tmp_path / "out")


def test_reports_malformed_nested_zip_and_keeps_safe_files(tmp_path: Path) -> None:
    archive = write_zip(
        tmp_path / "mixed.zip",
        {"broken.zip": b"not a zip", "safe.txt": b"safe"},
    )

    result = ArchiveExtractor(settings_for(tmp_path)).extract(archive, tmp_path / "out")

    assert [path.name for path in result.files] == ["safe.txt"]
    assert result.issues[0].relative_path == "broken.zip"
    assert "malformed" in result.issues[0].reason
