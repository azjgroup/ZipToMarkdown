from pathlib import Path


def test_launcher_is_local_and_uses_runtime_lock() -> None:
    script = Path("Start App.command").read_text(encoding="utf-8")
    assert "requirements.lock" in script
    assert "--host 127.0.0.1" in script
    assert "/health" in script
    assert 'open "http://127.0.0.1:$PORT"' in script
    assert "0.0.0.0" not in script


def test_launcher_selects_a_supported_python_and_handles_homebrew() -> None:
    script = Path("Start App.command").read_text(encoding="utf-8")
    assert "python3.12" in script
    assert "/opt/homebrew/bin" in script
    assert "sys.version_info >= (3, 10)" in script
    assert "sys.version_info < (3, 14)" in script


def test_launcher_reuses_dependencies_until_the_lock_changes() -> None:
    script = Path("Start App.command").read_text(encoding="utf-8")
    assert ".requirements.sha256" in script
    assert "shasum -a 256" in script
    assert "pip install -r requirements.lock" in script
