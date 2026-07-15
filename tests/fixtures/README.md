# Generated conversion fixtures

The real-format smoke tests generate their TXT, HTML, CSV, JSON, XML, DOCX, XLSX, PPTX, and PDF inputs inside pytest's temporary directory.

Keeping the generators in the test makes each fixture's expected text visible, avoids opaque binary files in Git, and verifies that the pinned MarkItDown stack handles standards-based files rather than project-specific samples.
