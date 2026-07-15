# Zip to Markdown — Design Specification

**Date:** 15 July 2026
**Status:** Approved for implementation planning

## Product summary

Zip to Markdown is a local-first macOS utility that accepts one ZIP archive, safely extracts its contents, converts every supported file with Microsoft's MarkItDown library, and returns one downloadable ZIP containing only Markdown files.

The app runs in the user's browser but binds only to `127.0.0.1`. Files never leave the Mac, there are no accounts or API keys, and conversion does not use cloud services. The source will be suitable for a public GitHub repository.

## Goals

- Provide a drag-and-drop workflow that a non-technical Mac user can launch by double-clicking a file.
- Reliably handle uploaded ZIP archives up to 1 GiB without loading the whole archive into memory.
- Preserve the archive's relative directory structure in the output.
- Convert files independently so one unsupported or malformed file does not stop the job.
- Provide visible upload and conversion progress.
- Return only Markdown files, plus a Markdown conversion report when any item is not converted.
- Keep all document processing local and automatically remove temporary data.

## Non-goals

- Hosting the app on a public server.
- Accepting individual files or folders outside a ZIP archive.
- Editing, previewing, or combining generated Markdown.
- Using Azure, OpenAI, third-party MarkItDown plugins, or any cloud OCR service.
- Preserving original files or extracted media in the download.
- Supporting multiple simultaneous conversion workers in the first version.
- Distributing a signed or notarized native `.app` bundle.

## User experience

The interface is a single responsive page with four states:

1. **Ready:** A drag-and-drop target and “Choose ZIP” button, with a 1 GiB limit and local-processing notice.
2. **Uploading:** The selected filename, bytes uploaded, percentage, and a progress bar.
3. **Converting:** The current relative filename, overall percentage, and converted, skipped, and failed counts.
4. **Complete:** A result summary, “Download Markdown ZIP” button, “Delete files” button, and a visible warning when the conversion report contains issues.

Validation errors appear inline and keep the page usable. The interface uses no CDN assets, external fonts, analytics, or remote scripts. It is keyboard accessible, has visible focus states, uses semantic status messages, and respects reduced-motion preferences.

## Architecture

The application supports Python 3.10 or newer and is developed and verified with Python 3.12. It uses FastAPI, Uvicorn, server-rendered HTML, local CSS, and small framework-free JavaScript. Microsoft MarkItDown is installed with its `all` optional dependency set so its locally available converters are enabled.

The application is split into focused components:

- **Web application:** Serves the page and exposes the local job API.
- **Upload service:** Streams multipart uploads into a per-job temporary directory while enforcing the compressed-size limit.
- **Archive service:** Validates and safely extracts the outer archive and nested archives.
- **Conversion service:** Discovers eligible files and invokes MarkItDown's restricted local-file API in a child process, one file at a time.
- **Job store:** Maintains in-memory status and paths for the current process, protected by a lock.
- **Result service:** Writes Markdown output and builds the downloadable result archive.
- **Cleanup service:** Deletes expired job directories and clears stale directories during startup.
- **Browser client:** Uploads with progress, polls job status, renders progress, downloads results, and requests deletion.

The first version runs one conversion worker. Additional jobs remain queued, which limits CPU and memory pressure on the Mac and makes progress reporting deterministic.

## Local API

- `GET /` returns the app page.
- `POST /api/jobs` accepts one multipart field named `archive` and returns a job identifier after the upload is safely persisted.
- `GET /api/jobs/{job_id}` returns the job state, filename, progress counts, current relative path, warnings, and download availability.
- `GET /api/jobs/{job_id}/download` streams the completed Markdown ZIP.
- `DELETE /api/jobs/{job_id}` removes all temporary data for that job and makes the identifier unavailable.
- `GET /health` returns a minimal local health response used by the launcher.

Job identifiers are random UUIDs. API handlers resolve job-owned paths from server state rather than accepting arbitrary paths from the browser.

## Data flow

1. The browser verifies that exactly one file with a `.zip` suffix was selected.
2. The upload service streams the body to a unique job directory in bounded chunks and rejects data beyond 1 GiB.
3. The archive service verifies the ZIP signature and central directory before extraction.
4. Safe entries are streamed to an `input/` tree while extraction counters enforce global limits.
5. Nested ZIP files are expanded recursively into sibling directories named `<archive-name>.contents`, to a maximum depth of three.
6. The conversion service creates an ordered list of regular files, excluding nested archives already expanded and known macOS metadata.
7. Each file is converted in an isolated child process using `MarkItDown(enable_plugins=False).convert_local(path)` and a 15-minute per-file timeout.
8. Successful text is written under `output/` using the source relative path with `.md` appended. For example, `reports/annual.pdf` becomes `reports/annual.pdf.md`.
9. Unsupported, unsafe, timed-out, or failed items are recorded without stopping later files.
10. If any item was skipped or failed, the result service adds `conversion-report.md` at the output root.
11. The output tree is packaged as `<original-archive-name>-markdown.zip` and becomes downloadable.
12. The job directory is deleted when requested, when it expires, or on the next app startup.

## Archive safety rules

The archive service enforces all limits across the outer archive and every nested archive:

- Maximum uploaded archive size: 1 GiB.
- Maximum extracted regular-file bytes: 5 GiB.
- Maximum archive entries: 10,000.
- Maximum nested ZIP depth: three.
- Maximum normalized relative path length: 1,024 characters.
- Absolute paths, `..` traversal, NUL bytes, device paths, and paths escaping the job root are rejected.
- Symbolic links, hard links represented by archive metadata, sockets, and other non-regular entries are rejected.
- Extracted files are created without following pre-existing links.
- Actual bytes written are counted while streaming; declared ZIP sizes are not trusted as the only defense.
- `.DS_Store` and `__MACOSX` metadata are skipped without being treated as conversion failures.

Crossing a global extraction limit stops archive extraction and marks the whole job failed before MarkItDown is invoked. Rejecting an individual unsafe entry records it in the report while allowing safe entries to continue, unless its metadata makes the archive structure unreliable.

## Conversion behavior

- MarkItDown version `0.1.6` is pinned for reproducible behavior.
- All conversion occurs on local filesystem paths through `convert_local()`.
- Third-party plugins are disabled.
- No LLM client, Azure endpoint, remote URI, or cloud credential is configured.
- Files are converted sequentially in stable relative-path order.
- Nested ZIP files are expanded by the archive service rather than converted into one combined Markdown document.
- A successful empty conversion still produces a `.md` file and is noted as empty in the job summary.
- Markdown is encoded as UTF-8 with normalized newline termination.
- The app does not rewrite MarkItDown's document content or attempt high-fidelity visual reproduction.

“Supported” means a locally installed MarkItDown converter accepts the file. Formats may include PDF, DOCX, PPTX, XLS/XLSX, HTML, CSV, JSON, XML, EPUB, text, images, audio, and other formats recognized by the pinned MarkItDown release. Conversion quality depends on MarkItDown and the source document; scanned or media-heavy content may be incomplete without cloud assistance, which is deliberately out of scope.

## Conversion report

`conversion-report.md` is included only when at least one archive entry or file is not converted. It contains:

- Original archive name and completion time.
- Converted, skipped, and failed totals.
- A table of relative paths, outcome, and a sanitized human-readable reason.
- A note that processing was local and that original files are not included.

Exceptions are logged locally with diagnostic detail, but the report excludes absolute temporary paths, stack traces, and machine-specific secrets.

If no source file converts successfully, the result ZIP still contains `conversion-report.md` so the user can download a complete explanation.

## Job lifecycle and cleanup

Job states are `uploading`, `queued`, `extracting`, `converting`, `packaging`, `complete`, `failed`, `deleting`, and `expired`. Progress responses include a monotonically increasing processed count so the browser never appears to move backward.

Completed and failed jobs expire one hour after their last user access. A periodic cleanup task removes expired job directories. Startup removes any leftover directories from an earlier process, so a quit or crash cannot leave document data indefinitely. Cleanup failures are retried and logged.

## Error handling

- Invalid selection, wrong extension, invalid signature, oversized upload, and malformed archive errors are shown before conversion begins.
- A global archive safety limit results in a failed job with no downloadable partial archive.
- Per-entry safety rejections, unsupported formats, conversion exceptions, and timeouts are reported while remaining safe files continue.
- Packaging failure marks the job failed and retains diagnostic logs until expiry.
- A missing or expired job returns a neutral `404` response.
- The browser provides retry guidance if polling is temporarily interrupted and resumes polling without starting another conversion.
- User-facing errors avoid stack traces and absolute filesystem paths.

## Launcher and installation

`Start App.command` is the user entry point. On first use it:

1. Locates `python3` and verifies Python 3.10 or newer.
2. Shows a readable installation message if no compatible Python exists.
3. Creates `.venv` inside the project directory.
4. Installs the pinned application dependencies from the lock file.
5. Starts Uvicorn on an available `127.0.0.1` port.
6. Waits for `/health`, then opens the app in the default browser.

Later launches reuse the environment when the lock-file fingerprint has not changed. The launcher keeps a small Terminal window open so the user can stop the server with Control-C or by closing the window. Network access may be required for the first dependency installation, but document processing itself remains offline.

## Repository structure

```text
ZipToMarkdown/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── settings.py
│   ├── jobs.py
│   ├── archives.py
│   ├── conversion.py
│   ├── results.py
│   ├── cleanup.py
│   ├── templates/index.html
│   └── static/
│       ├── app.css
│       └── app.js
├── tests/
│   ├── fixtures/
│   ├── test_archives.py
│   ├── test_conversion.py
│   ├── test_jobs.py
│   ├── test_api.py
│   └── test_browser.py
├── docs/superpowers/specs/
├── Start App.command
├── pyproject.toml
├── requirements.lock
├── README.md
├── LICENSE
└── .gitignore
```

Each Python module owns one responsibility. The FastAPI module wires services together but does not contain extraction or conversion rules.

## Testing strategy

### Unit tests

- Validate archive signature and size limits.
- Reject absolute, traversal, NUL-containing, and link entries.
- Enforce extracted-byte, entry-count, path-length, and nesting-depth limits using small test-configured thresholds.
- Verify safe nested extraction and stable discovery order.
- Verify `.DS_Store` and `__MACOSX` handling.
- Verify source-to-output naming, including identical stems with different extensions.
- Verify state transitions, progress monotonicity, expiry, and deletion.
- Verify report content is sanitized and generated only when needed.

### Integration tests

- Exercise upload, polling, successful download, delete, invalid ZIP, oversized stream, missing job, and expired job through FastAPI's test client.
- Inspect result archives to prove every member ends in `.md` and no original file is present.
- Run real local MarkItDown smoke conversions for representative PDF, DOCX, XLSX, PPTX, HTML, CSV, JSON, XML, and text fixtures.
- Verify a mixed archive returns successful Markdown alongside a failure report.

### Browser verification

- Exercise drag-and-drop and file-picker selection.
- Confirm upload and conversion states are announced and visibly updated.
- Confirm completion enables exactly one result download.
- Confirm error, expiry, and deletion states return to a usable page.
- Check keyboard navigation, narrow-window layout, and reduced-motion behavior.

## Acceptance criteria

- A compatible Mac can start the app by double-clicking `Start App.command` after cloning or downloading the repository.
- The server listens only on loopback and the interface identifies processing as local.
- A valid ZIP up to 1 GiB is streamed to disk and processed without holding the whole archive in application memory.
- Safe supported files, including files inside nested ZIPs to depth three, produce `.md` outputs in the matching directory structure.
- The downloaded archive contains no non-Markdown members.
- Unsupported or failed files do not prevent successful files from being downloaded.
- `conversion-report.md` accurately and safely explains every non-converted item.
- Unsafe archives cannot write outside the job directory or extract links and cannot exceed configured limits.
- Temporary job data is deleted on request, after one hour of inactivity, and during the next startup.
- Automated unit and integration tests pass, real MarkItDown fixtures convert, and the complete browser workflow is verified on macOS.
