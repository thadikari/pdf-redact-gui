import numpy as np
import pymupdf
import pytest

from redact_pdf.document import PdfDocument, PasswordRequired, PRESET_PATTERNS


def find_word(doc, pno, text):
    return next(w for w in doc.words(pno) if w.text == text)


def text_of(path, pno=0):
    with pymupdf.open(path) as d:
        return d[pno].get_text()


def test_words_and_select_text(text_pdf):
    doc = PdfDocument(text_pdf)
    assert doc.page_count == 2
    john = find_word(doc, 0, "John")
    assert 72 < john.rect[0] < 200 and 85 < john.rect[1] < 100
    # selecting from "John" to "Smith" gives one line rect covering both
    smith = find_word(doc, 0, "Smith")
    rects = doc.select_text(0, (john.rect[0] + 1, john.rect[1] + 2), (smith.rect[2] - 1, smith.rect[3] - 2))
    assert len(rects) == 1
    assert rects[0][0] <= john.rect[0] and rects[0][2] >= smith.rect[2]
    # selecting across two lines gives two rects
    email = find_word(doc, 0, "Email")
    assert len(doc.select_text(0, (john.rect[0], john.rect[1] + 2), (email.rect[2], email.rect[1] + 2))) == 2


@pytest.mark.parametrize("preset, expected", [
    ("Email addresses", "john.doe@example.com"),
    ("Phone numbers", "+1 555-123-4567"),
    ("Dates", "12/05/2024"),
    ("Long numbers (IDs, accounts)", "1234 5678 9012"),
])
def test_preset_search(text_pdf, preset, expected):
    doc = PdfDocument(text_pdf)
    res = doc.search(PRESET_PATTERNS[preset], regex=True)
    assert set(res) == {0, 1}
    assert any(expected in t for _, t in res[0]), res[0]


def test_plain_search_options(text_pdf):
    doc = PdfDocument(text_pdf)
    assert len(doc.search("john")[0]) == 2          # "John" and "john.doe"
    assert len(doc.search("john", case_sensitive=True).get(0, [])) == 1
    assert len(doc.search("john", whole_word=True)[0]) == 2   # \b also matches before "."
    assert doc.search("nothing-like-this") == {}


def test_true_redaction_removes_text_keeps_rest(text_pdf, tmp_path):
    doc = PdfDocument(text_pdf)
    hits = doc.search(PRESET_PATTERNS["Email addresses"], regex=True)
    marks = {p: [r for r, _ in h] for p, h in hits.items()}
    out = str(tmp_path / "out.pdf")
    leaks = doc.save(out, marks, scrub=True)
    assert leaks == []
    txt = text_of(out)
    assert "john.doe@example.com" not in txt and "example" not in txt
    assert "Public paragraph that must stay readable." in txt   # text layer kept
    with pymupdf.open(out) as d:
        assert d.page_count == 2
        assert not d.metadata.get("author") and not d.metadata.get("title")
        # black box drawn where the email was
        r = marks[0][0]
        pix = d[0].get_pixmap(clip=pymupdf.Rect(r[0] + 3, r[1] + 3, r[2] - 3, r[3] - 3))
        assert max(pix.samples) < 40


def test_scrub_off_keeps_metadata(text_pdf, tmp_path):
    out = str(tmp_path / "out.pdf")
    PdfDocument(text_pdf).save(out, {0: [(72, 85, 200, 105)]}, scrub=False)
    with pymupdf.open(out) as d:
        assert d.metadata["author"] == "Secret Author"


def test_flatten_has_no_text_and_same_size(text_pdf, tmp_path):
    out = str(tmp_path / "flat.pdf")
    leaks = PdfDocument(text_pdf).save(out, {0: [(72, 85, 200, 105)]}, flatten=True, dpi=100)
    assert leaks == []
    with pymupdf.open(out) as d:
        assert d.page_count == 2
        assert d[0].get_text().strip() == ""
        assert (d[0].rect.width, d[0].rect.height) == (612, 792)


def test_rotated_page_coordinates(rotated_pdf, tmp_path):
    doc = PdfDocument(rotated_pdf)
    assert doc.page_size(0) == (792, 612)
    john = find_word(doc, 0, "John")
    # display coordinates: render the word's rect and check it contains ink
    w, h, stride, samples, _ = doc.render(0, 2, clip=john.rect, gray=True)
    assert min(samples) < 100
    out = str(tmp_path / "rot_out.pdf")
    assert doc.save(out, {0: [john.rect]}) == []
    txt = text_of(out)
    assert "John" not in txt and "Smith" in txt


def test_image_pixels_removed(tmp_path):
    src = pymupdf.open()
    page = src.new_page(width=200, height=200)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
    pix.set_rect(pix.irect, (255, 0, 0))
    page.insert_image(page.rect, pixmap=pix)
    path = str(tmp_path / "img.pdf")
    src.save(path)
    out = str(tmp_path / "img_out.pdf")
    PdfDocument(path).save(out, {0: [(50, 50, 150, 150)]})
    with pymupdf.open(out) as d:
        xref = d[0].get_images()[0][0]
        img = pymupdf.Pixmap(d, xref)
        a = np.frombuffer(img.samples, np.uint8).reshape(img.height, img.width, img.n)
        cy, cx = img.height // 2, img.width // 2
        assert tuple(a[cy, cx, :3]) != (255, 0, 0), "image data under the box must be removed"
        assert tuple(a[2, 2, :3]) == (255, 0, 0), "image outside the box must be kept"


def test_overlapping_annotations_and_fields_removed(tmp_path):
    src = pymupdf.open()
    page = src.new_page()
    page.add_text_annot((100, 100), "secret note")
    page.add_text_annot((400, 600), "keep me")
    w = pymupdf.Widget()
    w.field_name, w.field_type = "ssn", pymupdf.PDF_WIDGET_TYPE_TEXT
    w.rect, w.field_value = pymupdf.Rect(80, 200, 250, 220), "123-45-6789"
    page.add_widget(w)
    path = str(tmp_path / "annots.pdf")
    src.save(path)
    out = str(tmp_path / "annots_out.pdf")
    PdfDocument(path).save(out, {0: [(90, 90, 260, 230)]})
    with pymupdf.open(out) as d:
        contents = [a.info["content"] for a in d[0].annots()]
        assert contents == ["keep me"]
        assert list(d[0].widgets()) == []


def test_overwrite_original_file(text_pdf):
    doc = PdfDocument(text_pdf)
    doc.save(text_pdf, {0: [find_word(doc, 0, "John").rect]})
    assert "John" not in text_of(text_pdf)


def test_password_protected(tmp_path, text_pdf):
    path = str(tmp_path / "locked.pdf")
    with pymupdf.open(text_pdf) as d:
        d.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="owner")
    with pytest.raises(PasswordRequired):
        PdfDocument(path)
    with pytest.raises(PasswordRequired):
        PdfDocument(path, "wrong")
    doc = PdfDocument(path, "pw")
    out = str(tmp_path / "unlocked_out.pdf")
    assert doc.save(out, {0: [find_word(doc, 0, "John").rect]}) == []
    assert "John" not in text_of(out) and "Smith" in text_of(out)


def test_scanned_page_ink_boxes(scanned_pdf):
    doc = PdfDocument(scanned_pdf)
    assert doc.words(0) == []
    boxes = doc.content_boxes(0)
    assert len(boxes) > 10
    # one ink box should hug the word "Name:" at x=72, baseline y=100
    assert any(abs(b[0] - 72) < 3 and 85 < b[1] < 95 and 96 < b[3] < 106 for b in boxes), boxes[:10]
    # boxes are word-sized, not whole lines
    assert all(b[2] - b[0] < 250 for b in boxes)


def test_text_page_content_boxes_are_words(text_pdf):
    doc = PdfDocument(text_pdf)
    assert len(doc.content_boxes(0)) == len(doc.words(0))


@pytest.mark.parametrize("fixture", ["text_pdf", "rotated_pdf"])
def test_verify_detects_text_left_in_place(request, fixture):
    path = request.getfixturevalue(fixture)
    doc = PdfDocument(path)
    john = find_word(doc, 0, "John")
    # the unredacted original must be reported as leaking at John's rect, but not elsewhere
    assert doc.verify(path, {0: [john.rect]}) == [(0, john.rect)]
    assert doc.verify(path, {0: [(400, 300, 450, 350)]}) == []
