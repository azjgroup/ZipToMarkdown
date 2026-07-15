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
