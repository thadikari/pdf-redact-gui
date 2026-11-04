"""Saving redaction progress, resuming it later, and autosave recovery."""
import json
import os
import shutil

import pytest
from PySide6.QtCore import QRectF, QPointF
from PySide6.QtWidgets import QFileDialog, QMessageBox, QDialog
from PySide6.QtTest import QTest

from redact_pdf import main_window as mw, session

from conftest import make_text_pdf

BOXES = {0: [(72, 85, 200, 105), (300, 300, 350, 330)], 1: [(72, 690, 120, 705)]}


@pytest.fixture
def env(app, tmp_path, monkeypatch):
    """Patched dialogs + factory for main windows. env.asked records resume prompts."""
    class Env:
        asked = []
        resume_answer = True
        save_path = None
        windows = []

    e = Env()
    e.asked = []
    monkeypatch.setattr(mw, "autosave_dir", lambda: str(tmp_path / "autosave"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: e.asked.append(("critical", a[2])))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    def ask(self, kind, info):
        e.asked.append((kind, session.count(info["marks"])))
        return e.resume_answer
    monkeypatch.setattr(mw.MainWindow, "_ask_resume", ask)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (e.save_path or a[2], ""))

    def make():
        w = mw.MainWindow()
        w.show()
        e.windows.append(w)
        return w
    e.make = make
    yield e
    for w in e.windows:
        w.undo.setClean()
        w.close()


@pytest.fixture
def pdf(tmp_path):
    return make_text_pdf(str(tmp_path / "doc.pdf"))


def add_boxes(win, boxes=BOXES):
    entries = [(win.view.pages[p], QRectF(QPointF(r[0], r[1]), QPointF(r[2], r[3])))
               for p, rs in boxes.items() for r in rs]
    win.view.add_marks(entries)


def rounded(marks):
    return {p: [tuple(round(v, 2) for v in r) for r in rs] for p, rs in marks.items()}


# ---------------------------------------------------------------- session file format

def test_session_roundtrip(tmp_path):
    path = str(tmp_path / "x.redactpdf")
    session.write(path, "C:/a/b.pdf", "abc", 3, {0: [(10.12345, 20, 30, 40)], 2: [(50, 60, 5, 6)], 1: []})
    info = session.read(path)
    assert info["pdf"] == {"path": os.path.abspath("C:/a/b.pdf"), "name": "b.pdf", "sha256": "abc", "pages": 3}
    assert info["marks"] == {0: [(10.123, 20, 30, 40)], 2: [(5, 6, 50, 60)]}   # normalized, empty dropped
    assert info["saved"] > 0


@pytest.mark.parametrize("content", ["not json", "{}", '{"format": "other"}',
                                     '{"format": "redactpdf-progress", "version": 99}',
                                     '{"format": "redactpdf-progress", "version": 1, "marks": {"0": [[1, 2]]}}'])
def test_session_rejects_bad_files(tmp_path, content):
    path = tmp_path / "bad.redactpdf"
    path.write_text(content)
    with pytest.raises(ValueError):
        session.read(str(path))


def test_sidecar_name():
    assert session.sidecar_path(os.path.join("d", "report.pdf")) == os.path.join("d", "report.redactpdf")


# ---------------------------------------------------------------- GUI flows

def test_save_progress_then_resume_later(env, pdf):
    w = env.make()
    w.open_file(pdf)
    add_boxes(w)
    assert w.isWindowModified()
    assert w.act_save.shortcut().toString() == "Ctrl+S"
    w.act_save.trigger()                         # Save Progress (dialog defaults to doc.redactpdf)
    side = session.sidecar_path(pdf)
    assert os.path.isfile(side) and w.project_path == side
    assert not w.isWindowModified()
    saved = rounded(w.view.marks())
    w.close()

    w2 = env.make()                              # "later": a fresh app window
    w2.open_file(pdf)
    assert env.asked == [("file", 3)]
    assert rounded(w2.view.marks()) == saved
    assert w2.project_path == side and not w2.isWindowModified()
    assert w2.thumbs.item(0).text().endswith("■ 2")
    # boxes loaded from progress are editable and undoable as usual
    w2.view.pages[0].marks()[0].setSelected(True)
    w2.act_delete.trigger()
    assert w2.view.mark_count() == 2
    w2.act_undo.trigger()
    assert w2.view.mark_count() == 3


def test_start_fresh_keeps_progress_file(env, pdf):
    w = env.make()
    w.open_file(pdf)
    add_boxes(w)
    w.save_progress()
    w.close()
    env.resume_answer = False
    w2 = env.make()
    w2.open_file(pdf)
    assert w2.view.mark_count() == 0
    assert os.path.isfile(session.sidecar_path(pdf))


def test_open_progress_file_directly_after_moving_both(env, pdf, tmp_path):
    w = env.make()
    w.open_file(pdf)
    add_boxes(w)
    w.save_progress()
    w.close()
    moved = tmp_path / "moved"
    moved.mkdir()
    shutil.move(pdf, moved / "doc.pdf")
    shutil.move(session.sidecar_path(pdf), moved / "doc.redactpdf")
    w2 = env.make()
    assert w2.open_file(str(moved / "doc.redactpdf"))
    assert w2.doc.path == str(moved / "doc.pdf")
    assert w2.view.mark_count() == 3
    assert env.asked == []                       # explicit open: no prompt


def test_open_progress_asks_to_locate_missing_pdf(env, pdf, tmp_path, monkeypatch):
    w = env.make()
    w.open_file(pdf)
    add_boxes(w)
    prog = str(tmp_path / "elsewhere.redactpdf")
    env.save_path = prog
    w.save_progress(choose=True)
    w.close()
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    shutil.move(pdf, hidden / "doc.pdf")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(hidden / "doc.pdf"), ""))
    w2 = env.make()
    assert w2.open_file(prog)
    assert w2.view.mark_count() == 3


def test_progress_for_a_different_pdf_warns(env, pdf, tmp_path, monkeypatch):
    w = env.make()
    w.open_file(pdf)
    add_boxes(w)
    w.save_progress()
    w.close()
    make_text_pdf(pdf, pages=1)                  # the PDF was replaced by another version
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No)
    w2 = env.make()
    assert not w2.open_file(session.sidecar_path(pdf))
    assert "not the same file" in asked[0]
    # opening the PDF itself does not offer the mismatched progress
    assert w2.open_file(pdf)
    assert env.asked == [] and w2.view.mark_count() == 0
    # answering yes loads the boxes that fit (page 2 no longer exists)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    w3 = env.make()
    assert w3.open_file(session.sidecar_path(pdf))
    assert w3.view.mark_count() == 2


def test_damaged_progress_file(env, tmp_path):
    bad = tmp_path / "bad.redactpdf"
    bad.write_text("garbage")
    w = env.make()
    assert not w.open_file(str(bad))
    assert env.asked[0][0] == "critical"


def test_autosave_recovers_after_crash(env, pdf):
    w = env.make()
    w.open_file(pdf)
    add_boxes(w)
    QTest.qWait(1800)                            # autosave fires ~1.5 s after the last change
    auto = session.autosave_file(mw.autosave_dir(), w.doc.fingerprint)
    assert os.path.isfile(auto)
    # "crash": the window is never closed properly
    w2 = env.make()
    w2.open_file(pdf)
    assert env.asked == [("auto", 3)]
    assert w2.view.mark_count() == 3
    assert w2.isWindowModified(), "recovered boxes are not saved yet"
    assert w2.project_path is None


def test_declining_recovery_deletes_autosave(env, pdf):
    w = env.make()
    w.open_file(pdf)
    add_boxes(w)
    w._autosave()
    auto = session.autosave_file(mw.autosave_dir(), w.doc.fingerprint)
    env.resume_answer = False
    w2 = env.make()
    w2.open_file(pdf)
    assert w2.view.mark_count() == 0 and not os.path.exists(auto)


def test_autosave_newer_than_progress_file_is_offered(env, pdf):
    w = env.make()
    w.open_file(pdf)
    add_boxes(w, {0: BOXES[0]})
    w.save_progress()
    add_boxes(w, {1: BOXES[1]})                   # more work after saving, then a crash
    w._autosave()
    w2 = env.make()
    w2.open_file(pdf)
    assert env.asked == [("auto", 3)]
    assert w2.view.mark_count() == 3
    assert w2.project_path == session.sidecar_path(pdf)   # next Ctrl+S updates the same file
    w2.save_progress()
    assert session.count(session.read(session.sidecar_path(pdf))["marks"]) == 3


def test_saving_progress_clears_autosave(env, pdf):
    w = env.make()
    w.open_file(pdf)
    add_boxes(w)
    w._autosave()
    auto = session.autosave_file(mw.autosave_dir(), w.doc.fingerprint)
    assert os.path.isfile(auto)
    w.save_progress()
    assert not os.path.exists(auto)


def test_close_prompt_save_progress(env, pdf, monkeypatch):
    w = env.make()
    w.open_file(pdf)
    add_boxes(w)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Save)
    assert w.maybe_save()
    assert os.path.isfile(session.sidecar_path(pdf))


def test_close_prompt_discard_removes_autosave(env, pdf):
    w = env.make()
    w.open_file(pdf)
    add_boxes(w)
    w._autosave()
    auto = session.autosave_file(mw.autosave_dir(), w.doc.fingerprint)
    assert w.maybe_save()                         # patched to "Discard"
    assert not os.path.exists(auto)


def test_export_keeps_boxes_resumable(env, pdf, tmp_path, monkeypatch):
    w = env.make()
    w.open_file(pdf)
    add_boxes(w)
    w.save_progress()
    add_boxes(w, {1: [(300, 300, 340, 320)]})
    out = str(tmp_path / "final.pdf")
    monkeypatch.setattr(mw.SaveDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (out, ""))
    assert w.export_pdf()
    assert os.path.isfile(out)
    assert not w.isWindowModified()
    # progress file updated to what was exported
    assert session.count(session.read(session.sidecar_path(pdf))["marks"]) == 4
    w.close()
    w2 = env.make()
    w2.open_file(pdf)
    assert w2.view.mark_count() == 4
