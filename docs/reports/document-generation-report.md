# Document generation report

- Generated at: 2026-10-14T04:13:09.172690+00:00
- Repository commit: `b4f8d8c719632279f3210e62f6a4da9cec6f9322`
- Repository state at generation: `M backend/.gitignore
 M docs/reports/Base64-Ops-Complete-Technical-Engineering-Book.md
 M docs/reports/Base64-Ops-Complete-Technical-Engineering-Book.pdf
 M docs/reports/file-coverage-report.md
 M docs/reports/source-file-inventory.json`
- Generator: `scripts/generate_engineering_book.py`
- PDF renderer: ReportLab core fonts with Pypdf structural validation.
- Source discovery: `git ls-files`; ignored `.env` files were never read.
- PDF readability check: True
- PDF page count: 425
- Outline entries observed by Pypdf: 37
- Rendered sample pages: C:\Users\KIIT0001\AppData\Local\Temp\base64-ops-book-samples\page-1.png, C:\Users\KIIT0001\AppData\Local\Temp\base64-ops-book-samples\page-3.png, C:\Users\KIIT0001\AppData\Local\Temp\base64-ops-book-samples\page-6.png, C:\Users\KIIT0001\AppData\Local\Temp\base64-ops-book-samples\page-12.png, C:\Users\KIIT0001\AppData\Local\Temp\base64-ops-book-samples\page-37.png, C:\Users\KIIT0001\AppData\Local\Temp\base64-ops-book-samples\page-57.png, C:\Users\KIIT0001\AppData\Local\Temp\base64-ops-book-samples\page-157.png, C:\Users\KIIT0001\AppData\Local\Temp\base64-ops-book-samples\page-404.png, C:\Users\KIIT0001\AppData\Local\Temp\base64-ops-book-samples\page-410.png, C:\Users\KIIT0001\AppData\Local\Temp\base64-ops-book-samples\page-425.png
- Manual visual inspection recorded: True
- Genuine deterministic product screenshots embedded: docs\assets\screenshots\safe-repair-demo.png

## Validation limitations

The generator validates each source page by its unique FILE REFERENCE marker and expected path. It validates that every included record has all eight required sections. It cannot turn local source inspection into a live verification of GitHub, Clerk, MongoDB Atlas, Redis, OpenAI, Render, Vercel, or a GitHub App. The deterministic screenshot, when present, is captured only from the public mutation-disabled /demo fixture and is explicitly not evidence of a live provider integration.

## Security handling

The generator reads only Git-tracked paths discovered from Git. It does not read ignored environment files or serialize repository source bodies into the book. It records source hashes and summaries for traceability. External references are official URLs and access dates, not copied provider material.
