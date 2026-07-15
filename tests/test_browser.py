import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import Page, expect, sync_playwright
from uvicorn import Config, Server

from app.main import create_app
from app.settings import Settings


@pytest.fixture
def page_client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(Settings(work_root=tmp_path / "jobs"))
    with TestClient(app) as client:
        yield client


def test_home_page_contains_accessible_workflow(page_client: TestClient) -> None:
    response = page_client.get("/")

    assert response.status_code == 200
    assert 'id="archive-input"' in response.text
    assert 'aria-live="polite"' in response.text
    assert 'role="alert"' in response.text
    assert "Files never leave this Mac" in response.text
    assert 'id="download-button"' in response.text
    assert 'id="delete-button"' in response.text
    assert 'id="retry-button"' in response.text
    assert '<button id="choose-button"' in response.text


def test_page_uses_only_local_assets(page_client: TestClient) -> None:
    response = page_client.get("/")

    assert "https://" not in response.text
    assert 'href="/static/app.css"' in response.text
    assert 'src="/static/app.js"' in response.text
    assert "innerHTML" not in page_client.get("/static/app.js").text


@pytest.fixture
def live_server(tmp_path: Path) -> Iterator[str]:
    app = create_app(
        Settings(
            work_root=tmp_path / "browser-jobs",
            conversion_timeout_seconds=30,
        )
    )
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        port = candidate.getsockname()[1]
    server = Server(Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        pytest.fail("The browser test server did not start.")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def browser_page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(accept_downloads=True)
        try:
            yield page
        finally:
            browser.close()


def write_browser_zip(path: Path) -> Path:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("notes.txt", "Browser conversion phrase")
    return path


def test_browser_completes_downloads_and_deletes_job(
    browser_page: Page,
    live_server: str,
    tmp_path: Path,
) -> None:
    source = write_browser_zip(tmp_path / "browser.zip")
    browser_page.goto(live_server)

    browser_page.set_input_files("#archive-input", source)

    expect(browser_page.locator("#complete-panel")).to_be_visible(timeout=30_000)
    expect(browser_page.locator("#complete-summary")).to_contain_text("1 file converted")
    with browser_page.expect_download() as download_info:
        browser_page.locator("#download-button").click()
    download_path = download_info.value.path()
    assert download_path is not None
    with ZipFile(download_path) as result:
        assert result.namelist() == ["notes.txt.md"]
        assert "Browser conversion phrase" in result.read("notes.txt.md").decode("utf-8")

    browser_page.locator("#delete-button").click()
    expect(browser_page.locator("#ready-panel")).to_be_visible()


def test_browser_rejects_non_zip_with_recovery_message(
    browser_page: Page,
    live_server: str,
    tmp_path: Path,
) -> None:
    invalid = tmp_path / "notes.txt"
    invalid.write_text("not a ZIP", encoding="utf-8")
    browser_page.goto(live_server)

    browser_page.set_input_files("#archive-input", invalid)

    expect(browser_page.locator("#error-message")).to_be_visible()
    expect(browser_page.locator("#error-message")).to_contain_text("ends in .zip")
    expect(browser_page.locator("#ready-panel")).to_be_visible()


def test_browser_is_keyboard_focusable_and_has_no_mobile_overflow(
    browser_page: Page,
    live_server: str,
) -> None:
    browser_page.set_viewport_size({"width": 390, "height": 844})
    browser_page.goto(live_server)

    choose_button = browser_page.get_by_role("button", name="Choose ZIP")
    choose_button.focus()

    assert browser_page.evaluate("document.activeElement.id") == "choose-button"
    assert browser_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
