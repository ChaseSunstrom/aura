---
name: reading-documents
version: 0.1.0
description: "Pull a figure, a table or the text out of a PDF, Word or Excel file — local or at a URL. Which reader to use, why web_extract cannot do it, and the wrong answer to avoid."
---

# Reading a document

"What does this PDF say", "what is the revenue for the East region", "summarise the spec at
this link". The deliverable is the **answer**, not the file: fetching it, checksumming it or
describing its type is not progress, and reporting one of those instead is the failure this
skill exists to prevent. A benchmark turn once downloaded a PDF, ran `md5sum` on it, and
answered that the checksum was unchanged. Nobody asked about the checksum.

## Steps

1. **Get it where a reader can see it.** The shell has no network (ADR-0045): `curl` there
   fails every time, so a document at a URL is downloaded by the browser — `browser_navigate`
   to the URL, or `browser_click` on its link — and lands in `/opt/data/files/<name>`.
   `host.docker.internal:PORT` resolves from the gateway to the docker host. A page of HTML is
   `web_extract`, not a download. A file someone gave you is already on disk: use its path.
2. **Confirm what it is** by reading it: `read_file` on the path returns a PDF, Word or Excel
   file as extracted text (Hermes converts the text layer) and returns an HTML error page
   saved under a `.pdf` name as HTML — that is the "not a document" signal. For a file under
   `/opt/data/work`, `od -A x -t x1z <path> | head -1` in the terminal says the same
   (a PDF starts `%PDF-`; a `.docx` or `.xlsx` is a zip and starts `PK`).
3. **Read it.** `read_file` gives the text of any of the three, wherever the file is. The
   readers below give tables, metadata and sheet lists, but they run in the shell, which sees
   only `/opt/data/work` (its cwd) and its own scratch — not `/opt/data/files`, where a
   download lands. They live under `/opt/hermes/skills/productivity/<name>/scripts/` and run
   under `/opt/hermes/.venv/bin/python`:

   | file | command |
   | --- | --- |
   | PDF text | `pdf_read.py --text <path>` — per-page text as JSON |
   | PDF tables | `pdf_read.py --tables <path>` — rows as JSON, `--csv-dir` to write CSVs |
   | PDF metadata | `pdf_read.py --meta <path>` — page count, sizes, encrypted/scanned flags |
   | Word | `docx_read.py --text <path>`, or `--structure` for headings and tables |
   | Excel | `xlsx_read.py --json <path>`, `--sheets` to list them, `--data-only` for values not formulas |

   A downloaded PDF's table is read off `read_file`'s text; say so when the columns did not
   survive the extraction rather than reconstructing them.

4. **Answer from the text you actually read**, quoting the row or line the figure came from.
   A number that is not in the extracted text is invented.

## Pitfalls

- **`web_extract` does not read documents.** It is Trafilatura, and Trafilatura is an HTML
  main-content extractor: handed a PDF it returns empty or nearly-empty content, which reads
  like an empty document rather than like a wrong tool. Do not conclude the file is blank.
  The browser toolset renders a PDF, it does not extract one — its job here is the download.
- **A scanned PDF has no text layer.** `read_file` flags the pages that need OCR;
  `pdf_read.py --meta` reports it; `--text` on one comes back empty or as a handful of stray
  characters. This image has no OCR stack installed, so say the PDF is scanned and unreadable
  here rather than reporting nothing found. Falling back to `vision_analyze` on a page image
  is worth one try for a single figure, and its answer is labelled as read off an image.
- **Only the Office libraries and the PDF readers are installed.** pdfplumber, pypdf,
  python-docx, openpyxl and python-pptx are in the image (making a docx, xlsx or pptx is the
  `making-documents` skill). reportlab, pymupdf and marker are not, so the PDF create and OCR
  halves of the bundled skills will report a missing dependency. That advice cannot be followed:
  `/opt/hermes` is owned by root and you run as `hermes`. Do not try to `pip install` it.
- **`execute_code` may be refused** on an unattended surface. Write the parsing to a file with
  `write_file` under `/opt/data/work` — the one part of the home the shell sees — and run it
  with `terminal` instead, the same fallback the image-inspection skill uses.
- A password-protected PDF needs `--password`; without one every read path fails with a
  decryption error, which is a locked file and not a corrupt one.
