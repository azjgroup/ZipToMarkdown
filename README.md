# Zip to Markdown

Zip to Markdown is a small, local Mac app that accepts a ZIP archive, safely unpacks it, converts its files with [Microsoft MarkItDown](https://github.com/microsoft/markitdown), and gives you a new ZIP containing only Markdown files.

Files are processed on your Mac. There is no account, cloud upload, analytics service, or external conversion API.

## Quick start on a Mac

1. Install Python 3.10–3.13 if needed. Python 3.12 is recommended and is available from [python.org](https://www.python.org/downloads/macos/) or Homebrew with `brew install python@3.12`.
2. Download the complete project folder, or clone this repository after it is published to GitHub.
3. Open the folder and double-click **Start App.command**.
4. On the first launch, macOS may ask for confirmation. Control-click **Start App.command**, choose **Open**, then choose **Open** again.
5. Drop a `.zip` file onto the page, wait for conversion, then choose **Download Markdown ZIP**.

The first launch needs an internet connection to install the pinned Python and MarkItDown components into a private `.venv` folder beside the app. Later launches reuse that installation and conversion runs locally. Keep the terminal window open while using the app; close it or press Control-C to stop the server.

## What you receive

The result is one downloadable ZIP containing only `.md` files. Original files are never added to the result. For example:

```text
source/
  proposal.docx
  figures/data.xlsx

result ZIP/
  proposal.docx.md
  figures/data.xlsx.md
  conversion-report.md
```

`conversion-report.md` summarizes successful, empty, skipped, and failed files. A problem with one file does not discard Markdown produced from the others.

Supported input types follow the pinned `markitdown[all]` 0.1.6 package. Common local formats include PDF, Word, PowerPoint, Excel, HTML, text, CSV, JSON, XML, images with readable metadata/OCR support, audio with supported local dependencies, and ZIP files nested inside the uploaded archive. Some encrypted, damaged, unusual, or unsupported files may be listed as failures in the report.

## Privacy and automatic cleanup

- The web server binds only to `127.0.0.1`, so it is accessible from this Mac rather than the local network.
- MarkItDown plugins are disabled, and this app does not configure remote AI or conversion services.
- Each upload uses an isolated temporary job folder.
- The uploaded ZIP and extracted originals are removed when processing finishes.
- Results expire after one hour and are also cleared when the app next starts.
- Choosing **Delete now** removes the result immediately.

Treat the downloaded Markdown as derived content: it can contain all readable text from the originals.

## Limits

- Maximum uploaded ZIP: 1 GiB
- Maximum total extracted data: 5 GiB
- Maximum archive entries: 10,000
- Maximum nested-ZIP depth: 3
- Maximum conversion time per file: 15 minutes
- Result retention: 1 hour
- Conversions run one at a time to keep memory use predictable

Unsafe archive paths, symbolic links, non-regular entries, and archives that exceed these limits are rejected or reported without writing outside the job folder.

## Terminal start

If double-clicking is unavailable, open Terminal, drag the project folder into it after `cd `, press Return, then run:

```bash
./Start\ App.command
```

The launcher automatically looks for a compatible Python, including Homebrew installations on Apple Silicon and Intel Macs. To select one explicitly:

```bash
ZIP_TO_MARKDOWN_PYTHON=/path/to/python3.12 ./Start\ App.command
```

## Troubleshooting

**macOS says the developer cannot be verified**  
Control-click **Start App.command**, choose **Open**, then confirm **Open**. This approval is normally needed only once.

**The launcher says Python is missing**  
Install Python 3.12 from python.org, close the terminal window, and double-click the launcher again. Python 3.14 is not currently used because the pinned conversion stack targets 3.10–3.13.

**Installation fails on first launch**  
Check the internet connection and available disk space, then run the launcher again. A partial installation is safely resumed or replaced.

**The page does not open**  
Leave the launcher window open and visit the `http://127.0.0.1:…` address shown there. If another copy is open, close both launcher windows and start once more.

**A file did not convert**  
Open `conversion-report.md` inside the downloaded result. Other successful Markdown files should still be present.

## Development

Use Python 3.12 for the tested contributor environment:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
.venv/bin/python -m playwright install chromium
.venv/bin/pytest -q
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
```

The app is MIT licensed. See [LICENSE](LICENSE).

