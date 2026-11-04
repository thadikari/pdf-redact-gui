import os
import sys

import pymupdf
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

LINES = [
    "Name: John Smith",
    "Email john.doe@example.com or call +1 555-123-4567",
    "Account 1234 5678 9012 opened 12/05/2024",
    "Public paragraph that must stay readable.",
]


def make_text_pdf(path, pages=2, rotate_last=False, metadata=True):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=612, height=792)
        for j, line in enumerate(LINES):
            page.insert_text((72, 100 + 30 * j), line, fontsize=12)
        page.insert_text((72, 700), f"Page {i + 1}", fontsize=10)
        if rotate_last and i == pages - 1:
            page.set_rotation(90)
    if metadata:
        doc.set_metadata({"author": "Secret Author", "title": "Secret Title"})
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def text_pdf(tmp_path):
    return make_text_pdf(str(tmp_path / "text.pdf"))


@pytest.fixture
def rotated_pdf(tmp_path):
    return make_text_pdf(str(tmp_path / "rotated.pdf"), pages=1, rotate_last=True)


@pytest.fixture
def scanned_pdf(tmp_path, text_pdf):
    """Image-only PDF (like a scan) made by rasterizing the text PDF."""
    src = pymupdf.open(text_pdf)
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=150)
        p = out.new_page(width=page.rect.width, height=page.rect.height)
        p.insert_image(p.rect, pixmap=pix)
    path = str(tmp_path / "scanned.pdf")
    out.save(path)
    return path


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      str(tmp_path_factory.mktemp("settings")))
    a = QApplication.instance() or QApplication([])
    a.setOrganizationName("RedactPDF-test")
    a.setApplicationName("RedactPDF-test")
    return a
