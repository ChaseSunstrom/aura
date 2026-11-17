---
name: document-convert
description: "Convert a document (docx, markdown, html, epub, or a PDF's text) to another format: convert-document.sh, which engine it picks, and what it cannot do without libreoffice."
version: 0.1.0
author: JARVIS
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Documents, Scripts]
    related_skills: [reading-documents, file-delivery]
---

# Converting a document

"Turn this docx into markdown", "make an epub of these notes", "give me the
text of that PDF", "just pages 3 to 5 of it" — all of it is one script:

```bash
convert-document.sh --in /opt/data/work/spec.docx --out /opt/data/work/spec.md
convert-document.sh --in /opt/data/work/notes.md --out /opt/data/work/notes.epub
convert-document.sh --in /opt/data/work/report.pdf --out /opt/data/work/report.txt
convert-document.sh --in /opt/data/work/report.pdf --out /opt/data/work/part.pdf --pages 3-5
```

It is on `PATH` in the image (`/usr/local/bin/convert-document.sh`, from the
scripts library in the repo). Call it by that name from any directory; do not
look for it under a path, and do not write your own `pandoc` line if the name is
not found — that is exit code 2's situation, below.

The formats come from the extensions; `--from` and `--to` override them with
pandoc's names (`markdown`, `gfm`, `html`, `docx`, `odt`, `epub`, `rst`,
`latex`, `plain`) when an extension lies. Which engine runs is decided by the
pair, and the JSON says which it was:

| In → out | Engine | Note |
|---|---|---|
| pdf → pdf | qpdf | `--pages 1-3` selects pages; without it the file is rewritten (repaired) |
| pdf → txt | pdftotext | the text and nothing else: pandoc has no PDF reader |
| pdf → anything else | pdftotext, then pandoc | the text goes in as commonmark, so layout is paragraphs, not the page |
| doc, ppt, pptx, xls, xlsx, odp, ods → anything | libreoffice | **optional in the image and not installed** (~500 MB, left out on purpose): exit 2 |
| anything → pdf | pandoc to docx, then libreoffice | pandoc needs a PDF engine and the image ships none: exit 2 until libreoffice is there |
| anything else | pandoc | the ordinary route |

A scanned PDF has no text to extract. That is exit 5, not an empty document,
and the answer is `pdftoppm` (render the pages to images) plus the vision
model — the `reading-documents` skill.

It prints **one line of JSON** and nothing else on stdout:

```json
{"ok":true,"path":"/opt/data/work/spec.md","bytes":48211,"engine":"pandoc"}
```

Read the field. What is on stderr is progress, not output.

## Where the files live

`--in` and `--out` must both be under `/opt/data/work` or `/opt/data/files`,
the two directories the shell shares with the rest of the house (ADR-0045).
Anything else is exit 3, on purpose. A document the person sent is under one of
them; a browser download lands in `/opt/data/files`; one you wrote is wherever
you wrote it, which should have been `/opt/data/work`. The shell cannot `curl`
a URL — there is no network in it — so a document that is only at a URL is a
browser download first, or `web_extract` if the person wants what it *says*
rather than the file.

## Do not rebuild this

The engine table above, the `--standalone` that makes an html or epub a whole
file rather than a fragment, the page selection through qpdf, the scanned-PDF
check: all in here. Reaching for `pandoc` or `pdftotext` directly is redoing
solved work with the checks missing.

## The exit codes mean different things

| Code | Meaning | What to do |
|---|---|---|
| 1 | Usage | Fix the arguments; do not retry as-is. `--out` needs an extension; `--pages` is for a PDF input |
| 2 | A tool is missing from the image | For pandoc, pdftotext or qpdf: file a capability gap and open the merge request; **never install it at runtime**. For libreoffice, say plainly that the house does not convert that format (it is optional, and the gap skill is the route if it is wanted) |
| 3 | A path is refused | Outside the two shared directories, or the input does not exist. Move or name the file; do not relax the check |
| 4 | The engine failed | pandoc has no reader for the input, or libreoffice does not convert that pair (a pptx to markdown is not a thing). Say which |
| 5 | The output is empty | A scanned PDF, or an engine that returned 0 and wrote nothing. Do not publish it |

Nothing is left behind on a failure, so a retry starts clean.

## After it succeeds

The path is inside the container. **Never answer with it.** Publish the file
with `artifact_publish` (`path` from the JSON, `mime` such as `text/markdown`,
`text/html`, `application/epub+zip` or `application/pdf`) and it shows where
the person is. If they asked what the document *says*, quote the answer from
the text and skip the publish — the deliverable is the answer, not the file.
