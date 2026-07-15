import asyncio
import contextlib
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit
from zipfile import is_zipfile

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from app.cleanup import cleanup_loop, clear_startup_workspace
from app.jobs import JobBusy, JobNotFound, JobStore
from app.processing import JobProcessor
from app.settings import Settings

ProcessorFactory = Callable[[Settings, JobStore], JobProcessor]
ALLOWED_ORIGIN_HOSTS = {"127.0.0.1", "::1", "localhost", "testserver"}
APP_DIRECTORY = Path(__file__).resolve().parent


def _default_processor(settings: Settings, store: JobStore) -> JobProcessor:
    return JobProcessor(settings, store)


def create_app(
    settings: Settings | None = None,
    processor_factory: ProcessorFactory | None = None,
) -> FastAPI:
    configured = settings or Settings()
    factory = processor_factory or _default_processor

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        clear_startup_workspace(configured.work_root)
        store = JobStore(configured.work_root)
        processor = factory(configured, store)
        application.state.settings = configured
        application.state.jobs = store
        application.state.processor = processor
        cleanup_task = asyncio.create_task(
            cleanup_loop(store, configured.job_ttl_seconds),
            name="job-cleanup",
        )
        try:
            yield
        finally:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task
            processor.shutdown()

    application = FastAPI(title="Zip to Markdown", lifespan=lifespan)
    templates = Jinja2Templates(directory=APP_DIRECTORY / "templates")
    application.mount(
        "/static",
        StaticFiles(directory=APP_DIRECTORY / "static"),
        name="static",
    )
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver", "[::1]"],
    )

    @application.middleware("http")
    async def reject_cross_origin_requests(request: Request, call_next: Callable):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).hostname not in ALLOWED_ORIGIN_HOSTS:
            return JSONResponse(
                {"detail": "Cross-origin requests are not allowed."},
                status_code=403,
            )
        return await call_next(request)

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="index.html")

    @application.post("/api/jobs", status_code=202)
    async def create_job(archive: UploadFile) -> dict[str, str]:
        raw_filename = archive.filename or ""
        filename = Path(PureWindowsPath(raw_filename).name).name
        if not filename or Path(filename).suffix.lower() != ".zip":
            raise HTTPException(status_code=400, detail="Choose one ZIP archive.")

        store: JobStore = application.state.jobs
        job = store.create(filename)
        upload_path = job.root / "upload.zip"
        uploaded = 0
        try:
            with upload_path.open("xb") as output:
                while chunk := await archive.read(configured.chunk_size):
                    uploaded += len(chunk)
                    if uploaded > configured.max_upload_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail="The ZIP exceeds the configured upload limit.",
                        )
                    output.write(chunk)
            if not is_zipfile(upload_path):
                raise HTTPException(
                    status_code=400,
                    detail="The uploaded file is not a valid ZIP archive.",
                )
        except HTTPException:
            store.delete(job.id, allow_active=True)
            raise
        except OSError as error:
            with contextlib.suppress(OSError):
                store.delete(job.id, allow_active=True)
            raise HTTPException(
                status_code=500,
                detail="The ZIP could not be saved on this Mac.",
            ) from error
        finally:
            await archive.close()

        application.state.processor.submit(job.id, upload_path)
        return {"job_id": job.id}

    @application.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, object]:
        try:
            return application.state.jobs.snapshot(job_id)
        except JobNotFound as error:
            raise HTTPException(
                status_code=404,
                detail="Conversion job was not found.",
            ) from error

    @application.get("/api/jobs/{job_id}/download")
    async def download_job(job_id: str) -> FileResponse:
        try:
            job = application.state.jobs.get(job_id)
        except JobNotFound as error:
            raise HTTPException(
                status_code=404,
                detail="Conversion job was not found.",
            ) from error
        if job.result_path is None or not job.result_path.is_file():
            raise HTTPException(status_code=409, detail="The result is not ready.")
        return FileResponse(
            job.result_path,
            media_type="application/zip",
            filename=job.result_path.name,
        )

    @application.delete("/api/jobs/{job_id}", status_code=204)
    async def delete_job(job_id: str) -> Response:
        try:
            application.state.jobs.delete(job_id)
        except JobNotFound as error:
            raise HTTPException(
                status_code=404,
                detail="Conversion job was not found.",
            ) from error
        except JobBusy as error:
            raise HTTPException(
                status_code=409,
                detail="Wait for conversion to finish before deleting files.",
            ) from error
        return Response(status_code=204)

    return application


app = create_app()
