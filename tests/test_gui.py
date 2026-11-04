"""Drive the real main window (offscreen) with synthetic mouse/keyboard events."""
import os

import pymupdf
import pytest
from PySide6.QtCore import Qt, QPointF, QPoint, QEvent, QSettings
from PySide6.QtGui import QMouseEvent, QWheelEvent, QKeyEvent
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox
from PySide6.QtTest import QTest

from redact_pdf import main_window as mw
from redact_pdf.view import RedactItem, TEXT, BOX, PAN
from redact_pdf.snapping import SNAP_PAD

SHOTS = os.environ.get("REDACT_SHOTS")  # optional folder for screenshots


@pytest.fixture
def win(app, text_pdf, monkeypatch, tmp_path):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(mw, "autosave_dir", lambda: str(tmp_path / "autosave"))
    w = mw.MainWindow()
    w.resize(1200, 900)
    w.show()
    app.processEvents()
    assert w.open_file(text_pdf)
    pump(app)
    yield w
    w.undo.setClean()
    w.close()


def pump(app, n=5):
    for _ in range(n):
        app.processEvents()


def vp_pos(win, pno, x, y):
    """Viewport position of page point (x, y)."""
    v = win.view
    return v.mapFromScene(v.pages[pno].mapToScene(QPointF(x, y)))


def mouse(widget, kind, pos, button=Qt.MouseButton.LeftButton, buttons=None,
          mods=Qt.KeyboardModifier.NoModifier):
    types = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
             "release": QEvent.Type.MouseButtonRelease, "dbl": QEvent.Type.MouseButtonDblClick}
    if buttons is None:
        buttons = Qt.MouseButton.NoButton if kind == "release" else button
    if kind == "move":
        button = Qt.MouseButton.NoButton
    p = QPointF(pos)
    ev = QMouseEvent(types[kind], p, QPointF(widget.mapToGlobal(pos)), button, buttons, mods)
    QApplication.sendEvent(widget, ev)


def drag(win, a, b, steps=6, mods=Qt.KeyboardModifier.NoModifier, button=Qt.MouseButton.LeftButton):
    vp = win.view.viewport()
    mouse(vp, "press", a, button, mods=mods)
    for i in range(1, steps + 1):
        mouse(vp, "move", QPoint(a.x() + (b.x() - a.x()) * i // steps, a.y() + (b.y() - a.y()) * i // steps),
              buttons=button, mods=mods)
    mouse(vp, "release", b, button, mods=mods)
    pump(QApplication.instance())


def word(win, text, pno=0):
    return next(w for w in win.doc.words(pno) if w.text == text)


def marks0(win):
    return win.view.marks().get(0, [])


def shot(win, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        pump(QApplication.instance())
        win.grab().save(os.path.join(SHOTS, name))


def test_open_shows_pages_and_thumbnails(win, app):
    assert win.stack.currentIndex() == 1
    assert len(win.view.pages) == 2
    assert win.thumbs.count() == 2
    assert win.page_total.text().strip() == "/ 2"
    assert "text.pdf" in win.windowTitle()
    QTest.qWait(250)
    assert win.view.pages[0].base is not None, "visible page must be rendered"
    shot(win, "01_opened.png")


def test_draw_box_snaps_to_word(win):
    john, smith = word(win, "John"), word(win, "Smith")
    win.view.set_zoom(2.0)
    win.view.go_to_page(0)
    # start a little outside John's top-left, end a little outside Smith's bottom-right
    # (less than half the ~4 pt word gap, otherwise the neighbouring word is the nearer edge)
    a = vp_pos(win, 0, john.rect[0] - 1.2, john.rect[1] - 1.5)
    b = vp_pos(win, 0, smith.rect[2] + 1.2, smith.rect[3] + 1.5)
    drag(win, a, b)
    m = marks0(win)
    assert len(m) == 1
    x0, y0, x1, y1 = m[0]
    assert abs(x0 - (john.rect[0] - SNAP_PAD)) < 0.01
    assert abs(x1 - (smith.rect[2] + SNAP_PAD)) < 0.01
    assert abs(y0 - (john.rect[1] - SNAP_PAD)) < 0.01
    assert abs(y1 - (smith.rect[3] + SNAP_PAD)) < 0.01
    shot(win, "02_box_snapped.png")


def test_alt_disables_snapping(win):
    john = word(win, "John")
    win.view.set_zoom(2.0)
    x_start = john.rect[0] - 2
    a = vp_pos(win, 0, x_start, john.rect[1] - 1.5)
    b = vp_pos(win, 0, john.rect[2] + 2.5, john.rect[3] + 1.5)
    drag(win, a, b, mods=Qt.KeyboardModifier.AltModifier)
    x0 = marks0(win)[0][0]
    assert abs(x0 - (john.rect[0] - SNAP_PAD)) > 0.2, "Alt must prevent snapping"


def test_click_without_drag_adds_nothing_and_undo_redo(win):
    v = win.view.viewport()
    p = vp_pos(win, 0, 300, 400)
    mouse(v, "press", p)
    mouse(v, "release", p)
    assert marks0(win) == []
    drag(win, vp_pos(win, 0, 300, 400), vp_pos(win, 0, 360, 440))
    assert len(marks0(win)) == 1
    assert win.windowTitle().endswith(" — RedactPDF") and win.isWindowModified()
    win.act_undo.trigger()
    assert marks0(win) == []
    win.act_redo.trigger()
    assert len(marks0(win)) == 1


def test_reverse_drag_and_page_clamp(win):
    win.view.set_zoom(1.0)
    win.view.go_to_page(0)
    drag(win, vp_pos(win, 0, 500, 300), vp_pos(win, 0, 400, 250), mods=Qt.KeyboardModifier.AltModifier)
    x0, y0, x1, y1 = marks0(win)[0]
    assert x0 < x1 and y0 < y1


def test_ctrl_wheel_zooms_at_cursor(win, app):
    v = win.view
    v.set_zoom(1.0)
    anchor = vp_pos(win, 0, 200, 300)
    before = v.pages[0].mapFromScene(v.mapToScene(anchor))
    ev = QWheelEvent(QPointF(anchor), QPointF(v.viewport().mapToGlobal(anchor)), QPoint(0, 0), QPoint(0, 720),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(v.viewport(), ev)
    pump(app)
    assert v.zoom > 3
    after = v.pages[0].mapFromScene(v.mapToScene(anchor))
    assert abs(after.x() - before.x()) < 2 and abs(after.y() - before.y()) < 2, "point under cursor stays put"
    assert win.zoom_box.currentText().endswith("%")


def test_zoom_actions_and_combo(win, app):
    v = win.view
    win.act_fit_width.trigger()
    fw = v.zoom
    win.act_fit_page.trigger()
    assert v.zoom < fw
    win._zoom_text("400%")
    assert abs(v.zoom - 4 * win._actual_zoom()) < 1e-6
    QTest.qWait(250)
    page = v.pages[v.current_page()]
    assert page.detail is not None, "high zoom renders a sharp detail tile"
    win.act_zoom_out.trigger()
    assert v.zoom < 4 * win._actual_zoom()
    for _ in range(40):
        win.act_zoom_in.trigger()
    assert v.zoom == 16.0


def test_text_tool_redacts_lines(win):
    win.view.set_tool(TEXT)
    win.view.set_zoom(1.5)
    name, email = word(win, "Name:"), word(win, "Email")
    drag(win, vp_pos(win, 0, name.rect[0] + 1, name.rect[1] + 3), vp_pos(win, 0, email.rect[2] - 1, email.rect[1] + 3))
    m = marks0(win)
    assert len(m) == 2, m      # one rect per line
    assert m[0][0] <= name.rect[0]


def test_double_click_redacts_word(win):
    john = word(win, "John")
    p = vp_pos(win, 0, (john.rect[0] + john.rect[2]) / 2, (john.rect[1] + john.rect[3]) / 2)
    mouse(win.view.viewport(), "dbl", p)
    m = marks0(win)
    assert len(m) == 1 and m[0][0] < john.rect[0] and m[0][2] > john.rect[2]


def test_move_resize_delete_and_nudge(win, app):
    v = win.view
    v.set_zoom(2.0)
    drag(win, vp_pos(win, 0, 300, 400), vp_pos(win, 0, 340, 430), mods=Qt.KeyboardModifier.AltModifier)
    item = v.pages[0].marks()[0]
    r0 = item.page_rect()
    # move by dragging the middle
    drag(win, vp_pos(win, 0, 320, 415), vp_pos(win, 0, 370, 465), mods=Qt.KeyboardModifier.AltModifier)
    r1 = item.page_rect()
    assert abs(r1.x() - (r0.x() + 50)) < 1 and abs(r1.y() - (r0.y() + 50)) < 1
    assert r1.size() == r0.size()
    # resize by dragging the right edge
    drag(win, vp_pos(win, 0, r1.right() - 0.5, r1.center().y()), vp_pos(win, 0, r1.right() + 30, r1.center().y()),
         mods=Qt.KeyboardModifier.AltModifier)
    r2 = item.page_rect()
    assert abs(r2.width() - (r1.width() + 30.5)) < 1 and r2.left() == r1.left()
    win.act_undo.trigger()
    assert item.page_rect() == r1
    # nudge with arrow keys
    item.setSelected(True)
    v.setFocus()
    QApplication.sendEvent(v, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier))
    assert abs(item.page_rect().x() - (r1.x() + 10)) < 1e-6
    # delete
    win.act_delete.trigger()
    assert marks0(win) == []
    win.act_undo.trigger()
    assert len(marks0(win)) == 1


def test_copy_to_all_pages_and_counts(win):
    drag(win, vp_pos(win, 0, 72, 690), vp_pos(win, 0, 200, 705), mods=Qt.KeyboardModifier.AltModifier)
    win.view.pages[0].marks()[0].setSelected(True)
    win.act_copy_all.trigger()
    assert len(win.view.marks()[1]) == 1
    assert "■ 1" in win.thumbs.item(1).text()
    assert win.marks_label.text() == "2 boxes on 2 pages"


def test_search_panel_redacts_matches(win):
    win._show_search()
    win.search.preset.setCurrentText("Email addresses")
    assert win.search.list.count() == 2
    assert win.view.highlights_for(0)
    win.search.redact(selected_only=False)
    assert len(win.view.marks()[0]) == 1 and len(win.view.marks()[1]) == 1
    assert win.view.highlights_for(0) == []
    # same matches again are not duplicated
    win.search.find()
    win.search.redact(selected_only=False)
    assert len(win.view.marks()[0]) == 1


def test_bad_regex_message(win):
    win.search.query.setText("([")
    win.search.regex.setChecked(True)
    win.search.find()
    assert "Invalid pattern" in win.search.summary.text()


def test_pan_tool_does_not_draw(win):
    win.view.set_tool(PAN)
    win.view.set_zoom(3)
    before = win.view.verticalScrollBar().value()
    drag(win, QPoint(400, 500), QPoint(400, 300))
    assert marks0(win) == []
    assert win.view.verticalScrollBar().value() > before


def test_save_flow_end_to_end(win, tmp_path, monkeypatch):
    out = str(tmp_path / "result.pdf")
    monkeypatch.setattr(mw.SaveDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (out, ""))
    john = word(win, "John")
    mouse(win.view.viewport(), "dbl", vp_pos(win, 0, john.rect[0] + 3, john.rect[1] + 5))
    win.view.set_preview(True)
    shot(win, "03_preview.png")
    assert win.export_pdf()
    assert not win.isWindowModified()
    with pymupdf.open(out) as d:
        txt = d[0].get_text()
    assert "John" not in txt and "Smith" in txt


def test_unsaved_prompt_on_close(win, monkeypatch):
    drag(win, vp_pos(win, 0, 300, 400), vp_pos(win, 0, 360, 440))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert win.maybe_save() is False
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Discard)
    assert win.maybe_save() is True


def test_scanned_pdf_snaps_to_ink(win, scanned_pdf):
    win.undo.setClean()
    assert win.open_file(scanned_pdf)
    v = win.view
    v.set_zoom(2.0)
    v.go_to_page(0)
    boxes = win.doc.content_boxes(0)
    name_box = min(boxes, key=lambda b: abs(b[0] - 72) + abs(b[1] - 90))
    a = vp_pos(win, 0, name_box[0] - 1.2, name_box[1] - 2)
    b = vp_pos(win, 0, name_box[2] + 1.2, name_box[3] + 2)
    drag(win, a, b)
    x0, y0, x1, y1 = marks0(win)[0]
    assert abs(x0 - (name_box[0] - SNAP_PAD)) < 0.01 and abs(x1 - (name_box[2] + SNAP_PAD)) < 0.01
