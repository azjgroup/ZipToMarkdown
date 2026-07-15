#!/bin/zsh
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"

fail_and_pause() {
  print ""
  print -r -- "$1"
  print ""
  read -r "?Press Return to close this window." || true
  exit 1
}

typeset -a PYTHON_CANDIDATES
PYTHON_CANDIDATES=(
  "${ZIP_TO_MARKDOWN_PYTHON:-}"
  "/opt/homebrew/bin/python3.13"
  "/opt/homebrew/bin/python3.12"
  "/opt/homebrew/bin/python3.11"
  "/opt/homebrew/bin/python3.10"
  "/usr/local/bin/python3.13"
  "/usr/local/bin/python3.12"
  "/usr/local/bin/python3.11"
  "/usr/local/bin/python3.10"
  "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13"
  "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12"
  "python3.13"
  "python3.12"
  "python3.11"
  "python3.10"
  "python3"
)

PYTHON_BIN=""
for candidate in "${PYTHON_CANDIDATES[@]}"; do
  [[ -n "$candidate" ]] || continue
  if [[ "$candidate" == */* ]]; then
    [[ -x "$candidate" ]] || continue
    resolved="$candidate"
  else
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
    [[ -n "$resolved" ]] || continue
  fi
  if "$resolved" -c \
    'import sys; raise SystemExit(not (sys.version_info >= (3, 10) and sys.version_info < (3, 14)))' \
    >/dev/null 2>&1; then
    PYTHON_BIN="$resolved"
    break
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  fail_and_pause \
    "Python 3.10–3.13 is required. Install Python 3.12 from https://www.python.org/downloads/macos/ and try again."
fi

VENV="$ROOT/.venv"
if [[ -x "$VENV/bin/python" ]] && ! "$VENV/bin/python" -c \
  'import sys; raise SystemExit(not (sys.version_info >= (3, 10) and sys.version_info < (3, 14)))' \
  >/dev/null 2>&1; then
  print "Refreshing an incompatible local environment…"
  /bin/rm -rf "$VENV"
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  print "Preparing Zip to Markdown for first use…"
  if ! "$PYTHON_BIN" -m venv "$VENV"; then
    fail_and_pause "The private Python environment could not be created."
  fi
fi

if [[ ! -f "$ROOT/requirements.lock" ]]; then
  fail_and_pause "requirements.lock is missing. Download the complete project folder and try again."
fi

FINGERPRINT="$(/usr/bin/shasum -a 256 requirements.lock | /usr/bin/awk '{print $1}')"
INSTALLED_FINGERPRINT="$(/bin/cat "$VENV/.requirements.sha256" 2>/dev/null || true)"
if [[ "$FINGERPRINT" != "$INSTALLED_FINGERPRINT" ]]; then
  print "Installing local conversion components…"
  if ! "$VENV/bin/python" -m pip install --upgrade pip; then
    fail_and_pause "Python's installer could not be updated. Check your internet connection and try again."
  fi
  if ! "$VENV/bin/python" -m pip install -r requirements.lock; then
    fail_and_pause "Conversion components could not be installed. Check your internet connection and try again."
  fi
  print -r -- "$FINGERPRINT" > "$VENV/.requirements.sha256"
fi

PORT="$("$VENV/bin/python" -c \
  'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

"$VENV/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" &
SERVER_PID=$!

READY=0
for attempt in {1..60}; do
  if /usr/bin/curl --silent --fail "http://127.0.0.1:$PORT/health" >/dev/null; then
    READY=1
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    break
  fi
  sleep 0.5
done

if [[ "$READY" -ne 1 ]]; then
  fail_and_pause "Zip to Markdown could not start. Review the messages above."
fi

print ""
print "Zip to Markdown is running privately on this Mac."
print "Close this window or press Control-C to stop it."
open "http://127.0.0.1:$PORT"
wait "$SERVER_PID" || true
