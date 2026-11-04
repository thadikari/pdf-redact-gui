"""Saving / loading redaction progress (boxes not yet applied) to a small JSON file."""
import hashlib
import json
import os
import tempfile
import time

EXT = ".redactpdf"
FORMAT = "redactpdf-progress"
VERSION = 1


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sidecar_path(pdf_path):
    """Default progress file for a PDF: report.pdf -> report.redactpdf (same folder)."""
    return os.path.splitext(pdf_path)[0] + EXT


def write(path, pdf_path, pdf_fingerprint, page_count, marks):
    """Atomically write progress. marks: {pno: [(x0, y0, x1, y1), ...]}"""
    data = {
        "format": FORMAT,
        "version": VERSION,
        "saved": time.time(),
        "pdf": {"path": os.path.abspath(pdf_path), "name": os.path.basename(pdf_path),
                "sha256": pdf_fingerprint, "pages": page_count},
        "marks": {str(p): [[round(v, 3) for v in r] for r in rects]
                  for p, rects in sorted(marks.items()) if rects},
    }
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def read(path):
    """Read and validate a progress file. Raises ValueError if it is not a valid one.

    Returns dict with keys: saved (float), pdf {path, name, sha256, pages}, marks {int: [tuple]}."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"Not a readable progress file: {e}") from e
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("Not a RedactPDF progress file.")
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise ValueError("This progress file was made by a newer version of RedactPDF.")
    pdf = data.get("pdf") or {}
    marks = {}
    try:
        for p, rects in (data.get("marks") or {}).items():
            pno = int(p)
            clean = []
            for r in rects:
                x0, y0, x1, y1 = (float(v) for v in r)
                clean.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
            if pno >= 0 and clean:
                marks[pno] = clean
    except (TypeError, ValueError) as e:
        raise ValueError(f"Damaged progress file: {e}") from e
    return {
        "saved": float(data.get("saved") or 0),
        "pdf": {"path": str(pdf.get("path") or ""), "name": str(pdf.get("name") or ""),
                "sha256": str(pdf.get("sha256") or ""), "pages": int(pdf.get("pages") or 0)},
        "marks": marks,
    }


def count(marks):
    return sum(len(v) for v in marks.values())


def autosave_file(folder, pdf_fingerprint):
    return os.path.join(folder, pdf_fingerprint[:32] + EXT)


def prune(folder, max_age_days=30):
    """Delete autosaves older than max_age_days."""
    if not os.path.isdir(folder):
        return
    cutoff = time.time() - max_age_days * 86400
    for name in os.listdir(folder):
        p = os.path.join(folder, name)
        try:
            if name.endswith(EXT) and os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass
