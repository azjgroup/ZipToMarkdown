import io
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient

from app.conversion import ConversionResult
from app.jobs import JobStore
from app.main import create_app
from app.processing import JobProcessor
from app.settings import Settings


class InlineExecutor(Executor):
    def submit(
        self,
        fn: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Future[Any]:
        future: Future[Any] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as error:
            future.set_exception(error)
        return future


class TextConverter:
    def convert(self, source: Path, output: Path) -> ConversionResult:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return ConversionResult("converted")


def valid_zip_bytes() -> bytes:
    stream = io.BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr("notes.txt", "hello")
    return stream.getvalue()


def zip_names(content: bytes) -> list[str]:
    with ZipFile(io.BytesIO(content)) as archive:
        return archive.namelist()


def synchronous_processor(settings: Settings, store: JobStore) -> JobProcessor:
    return JobProcessor(settings, store, TextConverter(), InlineExecutor())


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(work_root=tmp_path / "jobs")
    with TestClient(create_app(settings, synchronous_processor)) as test_client:
        yield test_client


def test_valid_upload_returns_downloadable_markdown_zip(client: TestClient) -> None:
    response = client.post(
        "/api/jobs",
        files={"archive": ("documents.zip", valid_zip_bytes(), "application/zip")},
    )

    assert response.status_code == 202
    job_id = response.json()["job_id"]
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "complete"
    download = client.get(f"/api/jobs/{job_id}/download")
    assert download.status_code == 200
    assert zip_names(download.content) == ["notes.txt.md"]
    assert download.headers["content-type"] == "application/zip"


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("notes.txt", b"not zip"),
        ("fake.zip", b"not zip"),
    ],
)
def test_invalid_uploads_are_rejected(
    name: str,
    content: bytes,
    client: TestClient,
) -> None:
    response = client.post("/api/jobs", files={"archive": (name, content)})

    assert response.status_code == 400


def test_upload_limit_is_enforced(tmp_path: Path) -> None:
    limited = Settings(work_root=tmp_path / "limited-jobs", max_upload_bytes=8)
    with TestClient(create_app(limited, synchronous_processor)) as test_client:
        response = test_client.post(
            "/api/jobs",
            files={"archive": ("large.zip", valid_zip_bytes(), "application/zip")},
        )

    assert response.status_code == 413
    assert list(limited.work_root.iterdir()) == []


def test_missing_job_is_not_found(client: TestClient) -> None:
    assert client.get("/api/jobs/missing").status_code == 404


def test_active_job_cannot_be_deleted(client: TestClient) -> None:
    job = client.app.state.jobs.create("active.zip")

    assert client.delete(f"/api/jobs/{job.id}").status_code == 409


def test_completed_job_can_be_deleted(client: TestClient) -> None:
    response = client.post(
        "/api/jobs",
        files={"archive": ("documents.zip", valid_zip_bytes(), "application/zip")},
    )
    job_id = response.json()["job_id"]

    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_health_is_readiness_contract(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_untrusted_host_is_rejected(client: TestClient) -> None:
    assert client.get("/health", headers={"host": "evil.example"}).status_code == 400


def test_cross_origin_request_is_rejected(client: TestClient) -> None:
    response = client.get(
        "/health",
        headers={"origin": "https://evil.example"},
    )

    assert response.status_code == 403
