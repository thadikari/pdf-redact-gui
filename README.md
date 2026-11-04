# RedactPDF

A desktop tool for redacting PDF documents. Draw boxes over sensitive content (or search for it), and
RedactPDF writes a copy where the content under the boxes is **removed from the file**, not just covered.

<img src="media/screenshot.PNG?raw=true" width="720" />

## Features
- **True redaction.** Text, image pixels and vector drawings under each box are deleted. The rest of
  the document keeps its selectable, searchable text. Optionally **flatten pages to images** for maximum safety.
- **Snapping.** Box edges snap to words, images and drawings (and to ink on scanned pages), with guide
  lines. Hold **Alt** to draw freely.
- **Magnifier.** A zoomed view around the cursor while you draw or resize, for precise placement.
- **Smooth zoom.** Ctrl+wheel or pinch zooms at the cursor, from 10% to 1600%. Pages are re-rendered
  sharply at every zoom level. Fit width, fit page, or type a zoom level.
- **Text tool.** Drag across text to redact it line by line, or double-click a word.
- **Search & redact.** Find text or patterns (emails, phone numbers, dates, long numbers, URLs, or your
  own regex) and redact all matches at once.
- Move and resize boxes, nudge them with the arrow keys, copy a box to every page (e.g. headers),
  and undo/redo everything.
- **Save progress and continue later.** Ctrl+S saves your boxes to a small `.redactpdf` file next to
  the PDF. Reopening the PDF (or the `.redactpdf` file) offers to resume where you left off. Boxes are
  also autosaved in the background, so a crash or a forgotten save doesn't lose your work.
- Preview the final result, page thumbnails, password-protected PDFs, and drag & drop.
- Metadata, attachments, JavaScript and thumbnails are removed on save (optional). After saving, the
  file is checked to confirm that no text remains under any box.

## Download / run
**Portable exe (Windows):** build it once (see below), then copy `dist\RedactPDF.exe` anywhere and run it.
Nothing needs to be installed on the target PC.

**From source** (Python 3.10+):
```
pip install -r requirements.txt
python main.py [file.pdf]
```

## Build the portable exe
In PowerShell, from the repo folder:
```
.\build.ps1
```
This creates `.venv`, installs the dependencies and PyInstaller, and writes `dist\RedactPDF.exe`
(about 65 MB, a single file).

## Working in several sittings
- **Save** (Ctrl+S) stores only your boxes in `report.redactpdf` beside `report.pdf`. The PDF is
  not changed. Keep both files together. You can move or rename the folder: if the PDF can't be found,
  you'll be asked to locate it.
- **Resume** by opening `report.pdf` again (you'll be asked "Resume / Start fresh") or by opening
  `report.redactpdf` directly.
- **Export PDF** (Ctrl+E) produces the final redacted file whenever you're done. You can still keep
  editing and export again later.
- The progress file stores a fingerprint of the PDF. If the PDF was changed or replaced, you'll be warned
  that the boxes may not line up.
- Autosaves live in `%APPDATA%\RedactPDF\RedactPDF\autosave` and are deleted after 30 days.
- A `.redactpdf` file holds box positions only, never the content under them. It is still a hint about
  where the sensitive parts are, so don't send it along with the redacted PDF.

## Shortcuts
| Key | Action |
| --- | --- |
| B / T / H | Box tool / Text tool / Hand tool |
| Drag | Draw a box (Box) / select text (Text) |
| Double-click | Redact the word under the cursor |
| Alt (while dragging) | Disable snapping |
| S / L / P | Toggle snapping / magnifier / preview |
| Ctrl+wheel, pinch | Zoom at the cursor |
| Ctrl+= / Ctrl+- | Zoom in / out |
| Ctrl+0 / Ctrl+1 / Ctrl+2 | Fit page / actual size / fit width |
| Space+drag, middle-drag | Pan |
| Arrows (+Shift) | Nudge selected boxes 1 pt (10 pt) |
| Delete | Delete selected boxes |
| Right click on a box | Delete, copy to all pages, zoom to box |
| Ctrl+Z / Ctrl+Y | Undo / redo |
| Ctrl+F | Search & redact |
| Ctrl+S / Ctrl+Shift+S | Save progress / save progress as… |
| Ctrl+E | Export the final redacted PDF |

## Development
```
pip install -r requirements-dev.txt
python -m pytest tests
```
The GUI tests run offscreen. `redact_pdf/document.py` holds all the PDF logic (PyMuPDF),
`snapping.py` the snapping math, `view.py` the page view and editing, and `main_window.py` the window,
panels and dialogs.

## License note
RedactPDF uses [PyMuPDF](https://pymupdf.readthedocs.io), which is licensed under the AGPL.
Distributing this program (or the exe) to others means doing so under AGPL terms, or buying a
commercial PyMuPDF licence.
