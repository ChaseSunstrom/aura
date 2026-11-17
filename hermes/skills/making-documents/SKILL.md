---
name: making-documents
version: 0.1.0
description: "Make a Word document, a spreadsheet or a slide deck (.docx, .xlsx, .pptx) when asked: python-docx, openpyxl and python-pptx scripts, then artifact_publish."
---

# Making a document, a spreadsheet or a slide deck

"Write me a one-page memo", "put these figures in a spreadsheet", "make a three-slide deck
about the trip". The deliverable is a **file he can open and send**, not the text of it in
the reply and not a path inside the container. The three libraries are in the image:
`python-docx` (Word), `openpyxl` (Excel) and `python-pptx` (PowerPoint). There is no network
in the shell and you cannot `pip install` anything, so use only these.

## Procedure

1. Write the script with `write_file` to `/opt/data/work/make_<name>.py`: the one part of the
   home the shell sees. Put the real content in it: the figures he gave you, text you wrote
   for the slide, not placeholders.
2. Run it with `terminal`: `/opt/hermes/.venv/bin/python /opt/data/work/make_<name>.py`
   (plain `python3` does not have the libraries). It writes the file next to the script.
3. Check the file exists and is not empty (`ls -l`). If the script raised, read the
   traceback and fix the script; do not publish a file you did not see written.
4. Publish it with `artifact_publish`: `path` is the file, `title` what he would call it
   ("Trip plan"), and `mime` as in the table. Give him the URL it returns in one sentence,
   saying what is in it. Never answer with the container path.

| Format | File name | `mime` for `artifact_publish` |
|---|---|---|
| Word | `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| Excel | `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| PowerPoint | `.pptx` | `application/vnd.openxmlformats-officedocument.presentationml.presentation` |

A mime left out is stored as `application/octet-stream`, which no surface treats as a document.
Artifacts expire after a day; pass `ttl: "never"` if he said to keep it.

## A document (.docx)

```python docx
from docx import Document

doc = Document()
doc.add_heading("Quarterly update", level=1)
doc.add_paragraph("Sales rose in the east and fell in the west.")
doc.add_heading("Figures", level=2)
table = doc.add_table(rows=1, cols=2)
table.style = "Table Grid"
table.rows[0].cells[0].text = "Region"
table.rows[0].cells[1].text = "Units"
for region, units in [("East", 150), ("West", 90)]:
    row = table.add_row().cells
    row[0].text, row[1].text = region, str(units)
doc.add_paragraph("Next steps:")
doc.add_paragraph("Hire in the west", style="List Bullet")
doc.save("/opt/data/work/update.docx")
```

## A spreadsheet (.xlsx)

Write numbers as numbers and totals as formulas, so the sheet still works when he edits it.

```python xlsx
from openpyxl import Workbook
from openpyxl.styles import Font

wb = Workbook()
ws = wb.active
ws.title = "Sales"
ws.append(["Region", "Units", "Price"])
for cell in ws[1]:
    cell.font = Font(bold=True)
for row in [("East", 150, 20.0), ("West", 90, 22.5)]:
    ws.append(row)
ws["A4"] = "Total"
ws["B4"] = "=SUM(B2:B3)"
ws.column_dimensions["A"].width = 16
wb.save("/opt/data/work/sales.xlsx")
```

A formula has no cached value until a spreadsheet program opens the file, so do not read the
total back with openpyxl to quote it; compute it in the script and say it in the reply.

## A slide deck (.pptx)

Layouts of the default template: 0 title, 1 title and bullets, 5 title only, 6 blank.

```python pptx
from pptx import Presentation

prs = Presentation()
title = prs.slides.add_slide(prs.slide_layouts[0])
title.shapes.title.text = "Trip to the coast"
title.placeholders[1].text = "Three days in June"

body = prs.slides.add_slide(prs.slide_layouts[1])
body.shapes.title.text = "Plan"
tf = body.placeholders[1].text_frame
tf.text = "Day 1: drive down"
for line in ["Day 2: the harbour", "Day 3: drive back"]:
    tf.add_paragraph().text = line

last = prs.slides.add_slide(prs.slide_layouts[1])
last.shapes.title.text = "Packing"
last.placeholders[1].text = "Sunscreen, tickets, charger"
last.notes_slide.notes_text_frame.text = "Mention the booking reference."
prs.save("/opt/data/work/trip.pptx")
```

## How it opens

The console, the desktop app and the phone show it as a document card with a link to open or
download it; it then opens in whatever Word, Excel or PowerPoint viewer that device has (a
browser has none of its own, so there it is a download). The terminal cannot show one: give
the link, or `artifact_send` it to the desktop or phone.

## What this does not do

- **No PDF.** `document-convert` turns a docx into markdown or epub and a PDF into text; a
  `.docx` to PDF needs libreoffice, which is not in the image. Say so rather than renaming.
- **No editing of a file he sent.** Reading one is `reading-documents`; this makes new files.
- **No charts, themes or brand templates, no images from the web.** A default-template deck
  with plain text is what you can promise. A picture he already has in `/opt/data/work` can
  go in with `slide.shapes.add_picture(path, left, top, width=...)` using
  `from pptx.util import Inches`.
- **Do not describe what you did not check.** Count the slides or rows you wrote and say
  that count; do not claim it "looks great".
