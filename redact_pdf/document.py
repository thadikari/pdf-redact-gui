"""PDF access layer (PyMuPDF): rendering, text/content geometry, search and saving.

All rectangles exposed by this module are in *display* coordinates: PDF points with the
origin at the top-left of the page as it is shown (i.e. page rotation already applied).
"""
import os
import re
import tempfile
from dataclasses import dataclass

import numpy as np
import pymupdf

Rect = tuple  # (x0, y0, x1, y1) in display points

# padding (points) added around text when it is selected/searched for redaction
TEXT_PAD = 1.0


class PasswordRequired(Exception):
    pass


@dataclass
class Word:
    rect: Rect
    text: str
    line_key: tuple  # (block_no, line_no) - words with the same key are on one line


def _union(rects):
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


def _pad(r, p=TEXT_PAD):
    return (r[0] - p, r[1] - p, r[2] + p, r[3] + p)


def rects_by_line(words):
    """Merge words into one rectangle per text line (keeps first-seen order)."""
    groups = {}
    for w in words:
        groups.setdefault(w.line_key, []).append(w.rect)
    return [_pad(_union(rs)) for rs in groups.values()]


PRESET_PATTERNS = {
    "Email addresses": r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
    "Phone numbers": r"(?<!\w)\+?\(?\d[\d\s().-]{6,}\d(?!\w)",
    "Dates": r"\b(?:\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{2,4})\b",
    "Long numbers (IDs, accounts)": r"\b\d[\d -]{4,}\d\b",
    "URLs": r"\b(?:https?://|www\.)\S+",
}


class PdfDocument:
    def __init__(self, path, password=None):
        self.path = path
        with open(path, "rb") as f:
            self._data = f.read()  # keep bytes so the file itself is never locked
        self.password = password
        self.doc = self._open_copy()
        self._words = {}
        self._content = {}

    def _open_copy(self):
        doc = pymupdf.open(stream=self._data, filetype="pdf")
        if doc.needs_pass:
            if not self.password or not doc.authenticate(self.password):
                doc.close()
                raise PasswordRequired()
        return doc

    # ---------- basic info ----------

    @property
    def fingerprint(self):
        """SHA-256 of the original file, used to match saved progress to this PDF."""
        if not hasattr(self, "_fingerprint"):
            from .session import fingerprint
            self._fingerprint = fingerprint(self._data)
        return self._fingerprint

    @property
    def page_count(self):
        return self.doc.page_count

    def page_size(self, pno):
        r = self.doc[pno].rect
        return r.width, r.height

    # ---------- rendering ----------

    def render(self, pno, scale, clip=None, gray=False):
        """Render page `pno` at `scale` pixels per point.

        Returns (width, height, stride, bytes, is_gray). `clip` is a display rect.
        """
        page = self.doc[pno]
        kwargs = dict(matrix=pymupdf.Matrix(scale, scale), alpha=False, annots=True)
        if gray:
            kwargs["colorspace"] = pymupdf.csGRAY
        if clip is not None:
            kwargs["clip"] = pymupdf.Rect(clip)  # get_pixmap clips in display coordinates
        pix = page.get_pixmap(**kwargs)
        return pix.width, pix.height, pix.stride, pix.samples, gray

    # ---------- text & content geometry ----------

    def _to_display(self, page, r):
        rr = pymupdf.Rect(r) * page.rotation_matrix
        return (rr.x0, rr.y0, rr.x1, rr.y1)

    def words(self, pno):
        if pno not in self._words:
            page = self.doc[pno]
            out = []
            for x0, y0, x1, y1, text, b, l, _w in page.get_text("words"):
                out.append(Word(self._to_display(page, (x0, y0, x1, y1)), text, (b, l)))
            self._words[pno] = out
        return self._words[pno]

    def content_boxes(self, pno):
        """Boxes that redaction edges should snap to: words, images, vector drawings,
        and (for scanned pages without text) ink regions detected in the rendering."""
        if pno in self._content:
            return self._content[pno]
        page = self.doc[pno]
        pw, ph = self.page_size(pno)
        boxes = [w.rect for w in self.words(pno)]
        has_images = False
        try:
            for info in page.get_image_info():
                r = self._to_display(page, info["bbox"])
                if r[2] - r[0] > 1 and r[3] - r[1] > 1:
                    has_images = True
                    # a full-page image (scan) is not a useful snap target
                    if (r[2] - r[0]) * (r[3] - r[1]) < 0.8 * pw * ph:
                        boxes.append(r)
        except Exception:
            pass
        try:
            drawings = page.get_drawings()
            if len(drawings) <= 3000:
                for d in drawings:
                    r = self._to_display(page, d["rect"])
                    w, h = r[2] - r[0], r[3] - r[1]
                    if (w > 2 or h > 2) and w * h < 0.5 * pw * ph:
                        boxes.append(r)
        except Exception:
            pass
        if not self.words(pno) and (has_images or not boxes):
            boxes.extend(self.ink_boxes(pno))
        self._content[pno] = boxes
        return boxes

    def ink_boxes(self, pno, scale=2.0):
        """Detect word-like ink regions on a rendered page (for scanned documents)."""
        w, h, stride, samples, _ = self.render(pno, scale, gray=True)
        img = np.frombuffer(samples, dtype=np.uint8).reshape(h, stride)[:, :w]
        ink = img < 140
        # ignore dark borders / scanner edges: columns or rows that are mostly ink
        ink[:, ink.mean(axis=0) > 0.6] = False
        ink[ink.mean(axis=1) > 0.6, :] = False

        def runs(mask, min_gap):
            idx = np.flatnonzero(mask)
            if idx.size == 0:
                return []
            breaks = np.flatnonzero(np.diff(idx) > min_gap)
            starts = np.concatenate(([idx[0]], idx[breaks + 1]))
            ends = np.concatenate((idx[breaks], [idx[-1]]))
            return list(zip(starts, ends))

        boxes = []
        row_ink = ink.sum(axis=1) >= 2
        for r0, r1 in runs(row_ink, 1):
            line_h = r1 - r0 + 1
            if line_h < 3:
                continue
            col_ink = ink[r0:r1 + 1].any(axis=0)
            for c0, c1 in runs(col_ink, max(3, int(line_h * 0.45))):
                if c1 - c0 < 2:
                    continue
                boxes.append((c0 / scale, r0 / scale, (c1 + 1) / scale, (r1 + 1) / scale))
        return boxes

    def word_at(self, pno, x, y):
        for w in self.words(pno):
            r = w.rect
            if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                return w
        return None

    def select_text(self, pno, p1, p2):
        """Words between two points in reading order, as one rect per line."""
        words = self.words(pno)
        if not words:
            return []

        def nearest(p):
            best, best_d = 0, None
            for i, w in enumerate(words):
                r = w.rect
                dx = max(r[0] - p[0], 0, p[0] - r[2])
                dy = max(r[1] - p[1], 0, p[1] - r[3])
                d = dx * dx + 4 * dy * dy  # prefer the same line
                if best_d is None or d < best_d:
                    best, best_d = i, d
            return best

        i, j = sorted((nearest(p1), nearest(p2)))
        return rects_by_line(words[i:j + 1])

    def search(self, query, regex=False, case_sensitive=False, whole_word=False):
        """Return {pno: [(rect, matched_text), ...]}. Matches are within a single line."""
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = query if regex else re.escape(query)
        if whole_word:
            pattern = r"\b(?:" + pattern + r")\b"
        rx = re.compile(pattern, flags)
        results = {}
        for pno in range(self.page_count):
            lines = {}
            for w in self.words(pno):
                lines.setdefault(w.line_key, []).append(w)
            found = []
            for line_words in lines.values():
                text, spans = "", []
                for w in line_words:
                    if text:
                        text += " "
                    spans.append((len(text), len(text) + len(w.text), w))
                    text += w.text
                for m in rx.finditer(text):
                    if m.end() == m.start():
                        continue
                    hit = [w.rect for s, e, w in spans if s < m.end() and e > m.start()]
                    if hit:
                        found.append((_pad(_union(hit)), m.group()))
            if found:
                results[pno] = found
        return results

    # ---------- saving ----------

    def save(self, out_path, marks, flatten=False, dpi=200, scrub=True, progress=None):
        """Write a redacted copy.

        marks: {pno: [display rect, ...]}. flatten: rasterize every page afterwards.
        progress: optional callable(done, total) -> False to cancel.
        Returns a list of (pno, rect) where text could still be extracted (should be empty).
        """
        doc = self._open_copy()
        total = doc.page_count * (2 if flatten else 1)
        step = 0

        def tick():
            nonlocal step
            step += 1
            if progress and progress(step, total) is False:
                raise InterruptedError()

        for pno in range(doc.page_count):
            page = doc[pno]
            rects = [pymupdf.Rect(r) * page.derotation_matrix for r in marks.get(pno, [])]
            if rects:
                # annotations and form fields overlapping a redaction would survive it
                for annot in list(page.annots() or []):
                    if any(annot.rect.intersects(r) for r in rects):
                        page.delete_annot(annot)
                for widget in list(page.widgets() or []):
                    if any(widget.rect.intersects(r) for r in rects):
                        page.delete_widget(widget)
                for r in rects:
                    page.add_redact_annot(r, fill=(0, 0, 0))
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                                      graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                      text=pymupdf.PDF_REDACT_TEXT_REMOVE)
            tick()

        if scrub:
            doc.scrub(attached_files=True, clean_pages=True, embedded_files=True,
                      hidden_text=False, javascript=True, metadata=True, redactions=False,
                      remove_links=False, reset_fields=False, reset_responses=True,
                      thumbnails=True, xml_metadata=True)
            doc.set_metadata({})

        if flatten:
            out = pymupdf.open()
            for page in doc:
                pix = page.get_pixmap(dpi=dpi, annots=True)
                new_page = out.new_page(width=page.rect.width, height=page.rect.height)
                new_page.insert_image(new_page.rect, pixmap=pix)
                tick()
            doc.close()
            doc = out

        # write to a temp file first, so overwriting the input file is safe
        folder = os.path.dirname(os.path.abspath(out_path))
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=folder)
        os.close(fd)
        try:
            doc.save(tmp, garbage=4, deflate=True, clean=True)
            doc.close()
            os.replace(tmp, out_path)
        except BaseException:
            doc.close()
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        return self.verify(out_path, marks)

    def verify(self, path, marks):
        """Check that no text (visible or invisible) remains inside the redacted areas.

        A character counts as a leak if the centre of its box lies inside a redaction rect.
        Returns [(pno, rect), ...]."""
        leaks = []
        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                doc.authenticate(self.password or "")
            for pno, rects in marks.items():
                page = doc[pno]
                centres = []
                raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
                for block in raw["blocks"]:
                    for line in block.get("lines", []):
                        for span in line["spans"]:
                            for ch in span["chars"]:
                                if ch["c"].strip():
                                    b = pymupdf.Rect(ch["bbox"]) * page.rotation_matrix
                                    centres.append(((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2))
                for r in rects:
                    if any(r[0] < x < r[2] and r[1] < y < r[3] for x, y in centres):
                        leaks.append((pno, r))
        return leaks
