import os
import re
import time

from PySide6.QtCore import Qt, QSize, QTimer, QSettings, QUrl, QStandardPaths
from PySide6.QtGui import (QAction, QActionGroup, QKeySequence, QUndoStack, QPixmap, QIcon,
                           QDesktopServices, QColor, QPainter)
from PySide6.QtWidgets import (QMainWindow, QFileDialog, QMessageBox, QInputDialog, QLineEdit,
                               QDockWidget, QListWidget, QListWidgetItem, QWidget, QVBoxLayout,
                               QHBoxLayout, QComboBox, QCheckBox, QPushButton, QLabel, QSpinBox,
                               QDialog, QRadioButton, QDialogButtonBox, QProgressDialog,
                               QApplication, QStackedWidget, QAbstractItemView, QToolButton,
                               QGroupBox, QListView)

from . import session
from .document import PdfDocument, PasswordRequired, PRESET_PATTERNS
from .view import PdfView, BOX, TEXT, PAN, to_pixmap
from .icons import icon, app_icon_image

APP_NAME = "RedactPDF"
PROGRESS_FILTER = f"RedactPDF progress (*{session.EXT})"
OPEN_FILTER = f"PDF or saved progress (*.pdf *{session.EXT});;PDF files (*.pdf);;{PROGRESS_FILTER};;All files (*)"


def autosave_dir():
    return os.path.join(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation),
                        "autosave")


def _when(ts):
    return time.strftime("%d %b %Y, %H:%M", time.localtime(ts)) if ts else "unknown time"


def _norm(marks):
    return {p: sorted(tuple(round(v, 1) for v in r) for r in rs) for p, rs in marks.items() if rs}

TOOL_HINTS = {
    BOX: "Drag to draw a box (snaps to text; hold Alt for free drawing) · Double-click a word · "
         "Ctrl+wheel zoom · Space/middle-drag pan",
    TEXT: "Drag across text to redact it line by line · Double-click a word",
    PAN: "Drag to move around · Ctrl+wheel to zoom",
}

SHORTCUTS_HELP = """
<h3>Drawing</h3>
<table cellpadding=3>
<tr><td><b>B</b> / <b>T</b> / <b>H</b></td><td>Box tool / Text tool / Hand (pan) tool</td></tr>
<tr><td>Drag</td><td>Draw a box (Box tool) or select text (Text tool)</td></tr>
<tr><td>Double-click</td><td>Redact the word under the cursor</td></tr>
<tr><td>Alt while dragging</td><td>Temporarily disable snapping</td></tr>
<tr><td><b>S</b></td><td>Toggle snapping</td></tr>
<tr><td><b>L</b></td><td>Toggle magnifier while drawing</td></tr>
<tr><td>Drag box / its edges</td><td>Move / resize a box</td></tr>
<tr><td>Arrow keys (+Shift)</td><td>Nudge selected boxes 1 pt (10 pt)</td></tr>
<tr><td>Delete</td><td>Delete selected boxes</td></tr>
<tr><td>Right click</td><td>Box menu: delete, copy to all pages, zoom to box</td></tr>
<tr><td>Ctrl+Z / Ctrl+Y</td><td>Undo / redo</td></tr>
</table>
<h3>Navigating</h3>
<table cellpadding=3>
<tr><td>Ctrl+wheel, pinch</td><td>Zoom at the cursor</td></tr>
<tr><td>Ctrl+= / Ctrl+-</td><td>Zoom in / out</td></tr>
<tr><td>Ctrl+0 / Ctrl+1 / Ctrl+2</td><td>Fit page / actual size / fit width</td></tr>
<tr><td>Space+drag, middle drag</td><td>Pan</td></tr>
<tr><td>Wheel / Shift+wheel</td><td>Scroll vertically / horizontally</td></tr>
<tr><td><b>P</b></td><td>Preview the final (solid black) result</td></tr>
<tr><td>Ctrl+F</td><td>Search &amp; redact</td></tr>
<tr><td>Ctrl+S</td><td>Save progress (boxes) to continue later</td></tr>
<tr><td>Ctrl+E</td><td>Export the final redacted PDF</td></tr>
</table>
"""


class ThumbnailList(QListWidget):
    WIDTH = 120

    def __init__(self, view):
        super().__init__()
        self.view = view
        self.doc = None
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.TopToBottom)
        self.setWrapping(False)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setIconSize(QSize(self.WIDTH, int(self.WIDTH * 1.5)))
        self.setSpacing(6)
        self.setUniformItemSizes(False)
        self.setMinimumWidth(self.WIDTH + 40)
        self._next = 0
        self._base = {}
        self._timer = QTimer(self, interval=0)
        self._timer.timeout.connect(self._render_some)
        self._syncing = False
        self.currentRowChanged.connect(self._on_row)
        view.current_page_changed.connect(self._on_view_page)
        view.marks_changed.connect(self.update_counts)

    def set_document(self, doc):
        self._timer.stop()
        self.clear()
        self._base = {}
        self.doc = doc
        if not doc:
            return
        for i in range(doc.page_count):
            w, h = doc.page_size(i)
            pm = QPixmap(self.WIDTH, max(1, int(self.WIDTH * h / w)))
            pm.fill(QColor(235, 235, 235))
            it = QListWidgetItem(QIcon(pm), str(i + 1))
            it.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.addItem(it)
        self._next = 0
        self._timer.start()

    def _render_some(self):
        t0 = time.perf_counter()
        dpr = self.devicePixelRatioF()
        while self._next < self.count() and time.perf_counter() - t0 < 0.03:
            i = self._next
            w, _h = self.doc.page_size(i)
            pm = to_pixmap(self.doc.render(i, self.WIDTH * dpr / w))
            pm.setDevicePixelRatio(dpr)
            self._base[i] = pm
            self._paint_icon(i)
            self._next += 1
        if self._next >= self.count():
            self._timer.stop()

    def _paint_icon(self, pno):
        """Thumbnail = page render with the page's redaction boxes drawn on top."""
        base = self._base.get(pno)
        if base is None:
            return
        marks = self.view.pages[pno].marks() if pno < len(self.view.pages) else []
        if not marks:
            self.item(pno).setIcon(QIcon(base))
            return
        pm = QPixmap(base)
        p = QPainter(pm)
        s = self.WIDTH / self.view.pages[pno].w   # logical px per point (painter honours the DPR)
        for m in marks:
            r = m.page_rect()
            p.fillRect(r.x() * s, r.y() * s, max(1.0, r.width() * s), max(1.0, r.height() * s),
                       QColor(0, 0, 0))
        p.end()
        self.item(pno).setIcon(QIcon(pm))

    def update_counts(self, pages):
        for pno in pages:
            it = self.item(pno)
            if it is not None:
                n = len(self.view.pages[pno].marks())
                it.setText(f"{pno + 1}   ■ {n}" if n else str(pno + 1))
                self._paint_icon(pno)

    def _on_row(self, row):
        if not self._syncing and row >= 0:
            self.view.go_to_page(row)

    def _on_view_page(self, pno):
        self._syncing = True
        self.setCurrentRow(pno)
        self._syncing = False


class SearchPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.results = {}
        lay = QVBoxLayout(self)

        self.preset = QComboBox()
        self.preset.addItem("Search for text…")
        self.preset.addItems(list(PRESET_PATTERNS))
        self.preset.currentIndexChanged.connect(self._on_preset)
        lay.addWidget(self.preset)

        row = QHBoxLayout()
        self.query = QLineEdit(placeholderText="Text or pattern")
        self.query.returnPressed.connect(self.find)
        find_btn = QPushButton("Find")
        find_btn.clicked.connect(self.find)
        row.addWidget(self.query)
        row.addWidget(find_btn)
        lay.addLayout(row)

        opts = QHBoxLayout()
        self.regex = QCheckBox("Regex")
        self.case = QCheckBox("Match case")
        self.whole = QCheckBox("Whole words")
        for cb in (self.regex, self.case, self.whole):
            opts.addWidget(cb)
        opts.addStretch()
        lay.addLayout(opts)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemClicked.connect(self._show_item)
        lay.addWidget(self.list, 1)

        btns = QHBoxLayout()
        self.redact_sel = QPushButton("Redact selected")
        self.redact_sel.clicked.connect(lambda: self.redact(selected_only=True))
        self.redact_all = QPushButton("Redact all")
        self.redact_all.clicked.connect(lambda: self.redact(selected_only=False))
        self.redact_all.setDefault(True)
        btns.addWidget(self.redact_sel)
        btns.addWidget(self.redact_all)
        lay.addLayout(btns)
        self._update_buttons()

    def _on_preset(self, idx):
        if idx > 0:
            self.query.setText(PRESET_PATTERNS[self.preset.currentText()])
            self.regex.setChecked(True)
            self.find()

    def reset(self):
        self.results = {}
        self.list.clear()
        self.summary.setText("")
        self.window.view.set_highlights({})
        self._update_buttons()

    def find(self):
        doc = self.window.doc
        q = self.query.text()
        if not doc or not q.strip():
            return
        try:
            res = doc.search(q, regex=self.regex.isChecked(), case_sensitive=self.case.isChecked(),
                             whole_word=self.whole.isChecked())
        except re.error as e:
            self.summary.setText(f"<span style='color:#d33'>Invalid pattern: {e}</span>")
            return
        self.results = res
        self.list.clear()
        n = 0
        for pno, hits in sorted(res.items()):
            for rect, text in hits:
                it = QListWidgetItem(f"p. {pno + 1}   {text}")
                it.setData(Qt.ItemDataRole.UserRole, (pno, rect))
                self.list.addItem(it)
                n += 1
        self.window.view.set_highlights({p: [r for r, _ in hits] for p, hits in res.items()})
        if n:
            self.summary.setText(f"{n} match{'es' if n != 1 else ''} on {len(res)} page(s). "
                                 "Highlighted in yellow.")
        elif not any(doc.words(i) for i in range(doc.page_count)):
            self.summary.setText("No text found. This PDF seems to be scanned images without a text "
                                 "layer, so search can't find anything; draw boxes instead.")
        else:
            self.summary.setText("No matches.")
        self._update_buttons()

    def _show_item(self, it):
        pno, rect = it.data(Qt.ItemDataRole.UserRole)
        self.window.view.show_rect(pno, rect)

    def redact(self, selected_only):
        items = self.list.selectedItems() if selected_only else \
            [self.list.item(i) for i in range(self.list.count())]
        marks = {}
        for it in items:
            pno, rect = it.data(Qt.ItemDataRole.UserRole)
            marks.setdefault(pno, []).append(rect)
        if not marks:
            return
        n = self.window.view.add_marks_by_index(marks, "Redact search matches")
        self.window.statusBar().showMessage(f"Added {n} box(es) from search", 4000)
        if selected_only:
            for it in items:
                self.list.takeItem(self.list.row(it))
            remaining = {}
            for i in range(self.list.count()):
                pno, rect = self.list.item(i).data(Qt.ItemDataRole.UserRole)
                remaining.setdefault(pno, []).append(rect)
            self.window.view.set_highlights(remaining)
            self.summary.setText(f"{self.list.count()} match(es) left.")
        else:
            self.list.clear()
            self.window.view.set_highlights({})
            self.summary.setText(f"Redacted {n} match(es).")
        self._update_buttons()

    def _update_buttons(self):
        self.redact_all.setEnabled(self.list.count() > 0)
        self.redact_sel.setEnabled(self.list.count() > 0)


class SaveDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle("Save redacted PDF")
        lay = QVBoxLayout(self)
        box = QGroupBox("Method")
        bl = QVBoxLayout(box)
        self.true_redact = QRadioButton("Remove content under boxes (recommended)")
        t1 = QLabel("Text, images and drawings under the boxes are deleted from the file. "
                    "All other text stays selectable and searchable, and the file stays small.")
        self.flatten = QRadioButton("Also flatten every page to an image (maximum safety)")
        t2 = QLabel("After removal, each page is converted to a picture. Nothing hidden can survive, "
                    "but text is no longer selectable and the file is larger.")
        for t in (t1, t2):
            t.setWordWrap(True)
            t.setStyleSheet("color: gray; margin-left: 22px;")
        dpi_row = QHBoxLayout()
        dpi_row.addSpacing(22)
        dpi_row.addWidget(QLabel("Resolution:"))
        self.dpi = QComboBox()
        self.dpi.addItems(["150 dpi", "200 dpi", "300 dpi"])
        dpi_row.addWidget(self.dpi)
        dpi_row.addStretch()
        bl.addWidget(self.true_redact)
        bl.addWidget(t1)
        bl.addWidget(self.flatten)
        bl.addWidget(t2)
        bl.addLayout(dpi_row)
        lay.addWidget(box)
        self.scrub = QCheckBox("Remove metadata (author, title…), attachments, JavaScript and thumbnails")
        lay.addWidget(self.scrub)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        flat = settings.value("save/flatten", False, type=bool)
        (self.flatten if flat else self.true_redact).setChecked(True)
        self.dpi.setCurrentText(settings.value("save/dpi", "200 dpi"))
        self.scrub.setChecked(settings.value("save/scrub", True, type=bool))
        self.flatten.toggled.connect(self.dpi.setEnabled)
        self.dpi.setEnabled(flat)
        self.setMinimumWidth(460)

    def options(self):
        return dict(flatten=self.flatten.isChecked(), dpi=int(self.dpi.currentText().split()[0]),
                    scrub=self.scrub.isChecked())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings()
        self.doc = None
        self.project_path = None      # .redactpdf file the boxes were last saved to / loaded from
        self._loading = False
        self._autosave_timer = QTimer(self, singleShot=True, interval=1500)
        self._autosave_timer.timeout.connect(self._autosave)
        session.prune(autosave_dir())
        self.undo = QUndoStack(self)
        self.view = PdfView(self.undo)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._welcome())
        self.stack.addWidget(self.view)
        self.setCentralWidget(self.stack)

        self.thumbs = ThumbnailList(self.view)
        self.thumbs_dock = QDockWidget("Pages", self)
        self.thumbs_dock.setObjectName("pages")
        self.thumbs_dock.setWidget(self.thumbs)
        self.thumbs_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable |
                                     QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.thumbs_dock)

        self.search = SearchPanel(self)
        self.search_dock = QDockWidget("Search && redact", self)
        self.search_dock.setObjectName("search")
        self.search_dock.setWidget(self.search)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.search_dock)
        self.search_dock.hide()
        self.search_dock.visibilityChanged.connect(self._on_search_visible)

        self._make_actions()
        self._make_toolbar()
        self._make_menus()
        self._make_statusbar()

        self.view.zoom_changed.connect(self._on_zoom)
        self.view.current_page_changed.connect(self._on_page)
        self.view.marks_changed.connect(self._on_marks)
        self.view.status.connect(self._show_status)
        self.view.tool_changed.connect(self._on_tool)
        self.undo.cleanChanged.connect(self._on_clean_changed)
        self.undo.indexChanged.connect(self._schedule_autosave)

        self.setAcceptDrops(True)
        self.setWindowIcon(QIcon(QPixmap.fromImage(app_icon_image(64))))
        self.resize(1200, 850)
        geo = self.settings.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        state = self.settings.value("window/state")
        if state is not None:
            self.restoreState(state)
        self.search_dock.hide()
        self.act_snap.setChecked(self.settings.value("view/snapping", True, type=bool))
        self.act_loupe.setChecked(self.settings.value("view/loupe", True, type=bool))
        self._update_title()
        self._update_actions()
        self._on_tool(BOX)

    # ------------------------------------------------------------------ UI construction

    def _welcome(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch()
        title = QLabel(f"<h1>{APP_NAME}</h1>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel("Black out sensitive parts of PDF documents.<br>"
                     "Open a PDF (or saved progress), or drop one onto this window.")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn = QPushButton(icon("open"), "  Open PDF…")
        btn.setIconSize(QSize(22, 22))
        btn.setMinimumSize(180, 44)
        btn.clicked.connect(lambda: self.open_file())
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(btn)
        row.addStretch()
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(16)
        lay.addLayout(row)
        lay.addStretch()
        return w

    def _action(self, text, slot=None, shortcut=None, ic=None, checkable=False, tip=None):
        a = QAction(text, self)
        if ic:
            a.setIcon(icon(ic))
        if shortcut:
            seqs = shortcut if isinstance(shortcut, list) else [shortcut]
            a.setShortcuts([QKeySequence(s) for s in seqs])
        if checkable:
            a.setCheckable(True)
        if slot:
            (a.toggled if checkable else a.triggered).connect(slot)
        tip = tip or text.replace("&", "").replace("…", "")
        if a.shortcut().toString():
            tip += f"  ({a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)})"
        a.setToolTip(tip)
        self.addAction(a)
        return a

    def _make_actions(self):
        v = self.view
        self.act_open = self._action("&Open…", lambda: self.open_file(), QKeySequence.StandardKey.Open, "open")
        self.act_save = self._action("&Save Progress", lambda: self.save_progress(),
                                     QKeySequence.StandardKey.Save, "save",
                                     tip="Save the boxes so you can close the app and continue later")
        self.act_save_as = self._action("Save Progress &As…", lambda: self.save_progress(choose=True),
                                        "Ctrl+Shift+S")
        self.act_export = self._action("&Export Redacted PDF…", self.export_pdf, "Ctrl+E", "export",
                                       tip="Apply the redactions and write the final PDF")
        self.act_close = self._action("&Close", self.close_document, QKeySequence.StandardKey.Close)
        self.act_quit = self._action("E&xit", self.close, QKeySequence.StandardKey.Quit)

        self.act_undo = self.undo.createUndoAction(self, "&Undo")
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_undo.setIcon(icon("undo"))
        self.act_redo = self.undo.createRedoAction(self, "&Redo")
        self.act_redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.act_redo.setIcon(icon("redo"))
        self.addActions([self.act_undo, self.act_redo])

        self.act_delete = self._action("&Delete Selected Boxes", v.delete_selected,
                                       [QKeySequence.StandardKey.Delete, "Backspace"])
        self.act_select_all = self._action("Select All Boxes on Page", v.select_all_on_page,
                                           QKeySequence.StandardKey.SelectAll)
        self.act_copy_all = self._action("Copy Selected Boxes to All Pages", self._copy_all)
        self.act_clear_page = self._action("Clear Boxes on This Page", v.clear_page)
        self.act_clear_all = self._action("Clear All Boxes", self._clear_all)

        self.tools = QActionGroup(self)
        self.act_box = self._action("&Box Tool", lambda: v.set_tool(BOX), "B", "box",
                                    tip="Box tool: draw redaction boxes")
        self.act_text = self._action("&Text Tool", lambda: v.set_tool(TEXT), "T", "text",
                                     tip="Text tool: select text to redact")
        self.act_pan = self._action("&Hand Tool", lambda: v.set_tool(PAN), "H", "pan",
                                    tip="Hand tool: pan around")
        for a in (self.act_box, self.act_text, self.act_pan):
            a.setCheckable(True)
            self.tools.addAction(a)
        self.act_box.setChecked(True)

        self.act_snap = self._action("&Snapping", self._set_snap, "S", "snap", checkable=True,
                                     tip="Snap box edges to text and other content")
        self.act_loupe = self._action("&Magnifier", self._set_loupe, "L", "loupe", checkable=True,
                                      tip="Show a magnifier while drawing")
        self.act_preview = self._action("&Preview Result", v.set_preview, "P", "preview", checkable=True,
                                        tip="Show boxes as they will look (solid black)")

        self.act_zoom_in = self._action("Zoom &In", v.zoom_in, [QKeySequence.StandardKey.ZoomIn, "Ctrl+="],
                                        "zoom_in")
        self.act_zoom_out = self._action("Zoom &Out", v.zoom_out, QKeySequence.StandardKey.ZoomOut, "zoom_out")
        self.act_fit_width = self._action("Fit &Width", v.fit_width, "Ctrl+2", "fit_width")
        self.act_fit_page = self._action("Fit &Page", v.fit_page, "Ctrl+0", "fit_page")
        self.act_actual = self._action("&Actual Size", lambda: v.set_zoom(self._actual_zoom()), "Ctrl+1")
        self.act_prev = self._action("Previous Page", lambda: v.go_to_page(v.current_page() - 1),
                                     "Ctrl+PgUp", "prev")
        self.act_next = self._action("Next Page", lambda: v.go_to_page(v.current_page() + 1),
                                     "Ctrl+PgDown", "next")
        self.act_find = self._action("&Find && Redact…", self._show_search, QKeySequence.StandardKey.Find,
                                     "search", tip="Search text or patterns (emails, phone numbers…) and redact them")
        self.act_help = self._action("&Keyboard Shortcuts", self._show_help, QKeySequence.StandardKey.HelpContents)
        self.act_about = self._action("&About", self._show_about)

    def _make_toolbar(self):
        tb = self.addToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(22, 22))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for a in (self.act_open, self.act_save, self.act_export):
            tb.addAction(a)
        self.act_save.setIconText("Save")
        self.act_export.setIconText("Export PDF")
        tb.addSeparator()
        tb.addAction(self.act_undo)
        tb.addAction(self.act_redo)
        tb.addSeparator()
        for a, label in ((self.act_box, "Box"), (self.act_text, "Text"), (self.act_pan, "Hand")):
            a.setIconText(label)
            tb.addAction(a)
        tb.addSeparator()
        for a, label in ((self.act_snap, "Snap"), (self.act_loupe, "Magnifier"), (self.act_preview, "Preview")):
            a.setIconText(label)
            tb.addAction(a)
        tb.addSeparator()
        icon_only = []
        for a in (self.act_zoom_out,):
            tb.addAction(a)
            icon_only.append(a)
        self.zoom_box = QComboBox()
        self.zoom_box.setEditable(True)
        self.zoom_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.zoom_box.addItems(["Fit width", "Fit page", "50%", "75%", "100%", "125%", "150%", "200%",
                                "300%", "400%", "800%", "1600%"])
        self.zoom_box.setMinimumContentsLength(7)
        self.zoom_box.textActivated.connect(self._zoom_text)
        self.zoom_box.lineEdit().returnPressed.connect(lambda: self._zoom_text(self.zoom_box.currentText()))
        tb.addWidget(self.zoom_box)
        for a in (self.act_zoom_in, self.act_fit_width, self.act_fit_page):
            tb.addAction(a)
            icon_only.append(a)
        tb.addSeparator()
        tb.addAction(self.act_prev)
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.page_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.page_spin.setMinimumWidth(48)
        self.page_spin.valueChanged.connect(lambda n: self.view.go_to_page(n - 1))
        tb.addWidget(self.page_spin)
        self.page_total = QLabel(" / 0 ")
        tb.addWidget(self.page_total)
        tb.addAction(self.act_next)
        icon_only += [self.act_prev, self.act_next, self.act_undo, self.act_redo]
        tb.addSeparator()
        tb.addAction(self.act_find)
        self.act_find.setIconText("Find")
        for a in icon_only:
            w = tb.widgetForAction(a)
            if isinstance(w, QToolButton):
                w.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

    def _make_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction(self.act_open)
        m.addSeparator()
        m.addActions([self.act_save, self.act_save_as])
        m.addSeparator()
        m.addAction(self.act_export)
        m.addSeparator()
        m.addAction(self.act_close)
        m.addSeparator()
        m.addAction(self.act_quit)
        m = mb.addMenu("&Edit")
        m.addActions([self.act_undo, self.act_redo])
        m.addSeparator()
        m.addActions([self.act_delete, self.act_select_all, self.act_copy_all])
        m.addSeparator()
        m.addActions([self.act_clear_page, self.act_clear_all])
        m.addSeparator()
        m.addAction(self.act_find)
        m = mb.addMenu("&View")
        m.addActions([self.act_zoom_in, self.act_zoom_out, self.act_actual, self.act_fit_width, self.act_fit_page])
        m.addSeparator()
        m.addActions([self.act_prev, self.act_next])
        m.addSeparator()
        m.addActions([self.act_preview, self.act_loupe])
        m.addAction(self.thumbs_dock.toggleViewAction())
        m = mb.addMenu("&Tools")
        m.addActions([self.act_box, self.act_text, self.act_pan])
        m.addSeparator()
        m.addAction(self.act_snap)
        m = mb.addMenu("&Help")
        m.addActions([self.act_help, self.act_about])

    def _make_statusbar(self):
        sb = self.statusBar()
        self.hint_label = QLabel()
        self.hint_label.setStyleSheet("color: gray;")
        self.marks_label = QLabel()
        sb.addPermanentWidget(self.hint_label)
        sb.addPermanentWidget(self.marks_label)

    # ------------------------------------------------------------------ state updates

    # bound methods (not lambdas) so Qt drops these connections when the window is destroyed
    def _show_status(self, msg):
        self.statusBar().showMessage(msg, 3000)

    def _on_clean_changed(self, clean):
        self.setWindowModified(not clean)

    def _on_search_visible(self, visible):
        if not visible:
            self.view.set_highlights({})

    def _update_title(self):
        name = os.path.basename(self.doc.path) if self.doc else None
        self.setWindowTitle(f"{name}[*] — {APP_NAME}" if name else APP_NAME)

    def _update_actions(self):
        has = self.doc is not None
        for a in (self.act_save, self.act_save_as, self.act_export, self.act_close, self.act_delete,
                  self.act_select_all, self.act_copy_all,
                  self.act_clear_page, self.act_clear_all, self.act_zoom_in, self.act_zoom_out,
                  self.act_fit_width, self.act_fit_page, self.act_actual, self.act_prev, self.act_next,
                  self.act_find, self.act_preview):
            a.setEnabled(has)
        self.zoom_box.setEnabled(has)
        self.page_spin.setEnabled(has)
        self.stack.setCurrentIndex(1 if has else 0)
        self.thumbs_dock.setVisible(has)

    def _actual_zoom(self):
        return self.logicalDpiX() / 72.0

    def _on_zoom(self, z):
        self.zoom_box.setEditText(f"{round(z / self._actual_zoom() * 100)}%")

    def _zoom_text(self, text):
        t = text.strip().lower()
        if t.startswith("fit w"):
            self.view.fit_width()
        elif t.startswith("fit p"):
            self.view.fit_page()
        else:
            try:
                pct = float(t.rstrip("%").strip())
                self.view.set_zoom(pct / 100 * self._actual_zoom())
            except ValueError:
                self._on_zoom(self.view.zoom)
        self.view.setFocus()

    def _on_page(self, pno):
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(pno + 1)
        self.page_spin.blockSignals(False)

    def _on_marks(self, _pages=None):
        n = self.view.mark_count()
        pages = len(self.view.marks())
        self.marks_label.setText(f"{n} box{'es' if n != 1 else ''} on {pages} page{'s' if pages != 1 else ''}"
                                 if n else "No boxes yet")

    def _on_tool(self, tool):
        {BOX: self.act_box, TEXT: self.act_text, PAN: self.act_pan}[tool].setChecked(True)
        self.hint_label.setText(TOOL_HINTS[tool])

    def _set_snap(self, on):
        self.view.snapping = on
        self.settings.setValue("view/snapping", on)

    def _set_loupe(self, on):
        self.view.loupe_enabled = on
        self.settings.setValue("view/loupe", on)

    def _show_search(self):
        self.search_dock.show()
        self.search_dock.raise_()
        self.search.query.setFocus()
        self.search.query.selectAll()

    def _copy_all(self):
        n = self.view.copy_to_all_pages()
        self.statusBar().showMessage(f"Copied to other pages: {n} box(es)" if n else
                                     "Select one or more boxes first", 4000)

    def _clear_all(self):
        if self.view.mark_count() and QMessageBox.question(
                self, APP_NAME, "Remove all redaction boxes from every page?") == QMessageBox.StandardButton.Yes:
            self.view.clear_all()

    def _show_help(self):
        QMessageBox.information(self, "Keyboard shortcuts", SHORTCUTS_HELP)

    def _show_about(self):
        import pymupdf
        from PySide6 import __version__ as qt_ver
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<h3>{APP_NAME}</h3><p>Redact PDF documents: the content under each box "
                          f"is removed from the file, not just covered.</p>"
                          f"<p>PyMuPDF {pymupdf.VersionBind} · Qt {qt_ver}</p>")

    # ------------------------------------------------------------------ open / save / close

    def maybe_save(self):
        """Ask about unsaved boxes. Returns False if the caller should abort."""
        if not self.doc or self.undo.isClean() or (self.view.mark_count() == 0 and not self.project_path):
            return True
        r = QMessageBox.warning(self, APP_NAME, "Your redaction boxes are not saved.\n\n"
                                                "Save your progress so you can continue later?",
                                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            return self.save_progress()
        if r == QMessageBox.StandardButton.Discard:
            self._remove_autosave()
            return True
        return False

    def open_file(self, path=None):
        if not self.maybe_save():
            return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Open", self.settings.value("last_dir", ""), OPEN_FILTER)
            if not path:
                return False
        self.settings.setValue("last_dir", os.path.dirname(path))
        if path.lower().endswith(session.EXT):
            return self.open_progress(path)
        doc = self._load_pdf(path)
        if doc is None:
            return False
        self._show_document(doc)
        self._offer_resume()
        return True

    def _load_pdf(self, path):
        password = None
        while True:
            try:
                doc = PdfDocument(path, password)
                break
            except PasswordRequired:
                password, ok = QInputDialog.getText(
                    self, "Password required", f"“{os.path.basename(path)}” is protected.\nPassword:",
                    QLineEdit.EchoMode.Password)
                if not ok:
                    return None
            except Exception as e:
                QMessageBox.critical(self, APP_NAME, f"Could not open the file:\n{path}\n\n{e}")
                return None
        if doc.page_count == 0:
            QMessageBox.critical(self, APP_NAME, "This PDF has no pages.")
            return None
        return doc

    def _show_document(self, doc):
        self._loading = True
        self._autosave_timer.stop()
        self.doc = doc
        self.project_path = None
        self.search.reset()
        self._update_actions()
        self.view.set_document(doc)
        self.thumbs.set_document(doc)
        self.page_spin.setMaximum(doc.page_count)
        self.page_total.setText(f" / {doc.page_count} ")
        self.undo.setClean()
        self._update_title()
        self._on_marks()
        self.view.setFocus()
        self._loading = False

    # ------------------------------------------------------------------ saved progress

    def _apply_marks(self, marks, unsaved):
        """Show saved boxes. unsaved=True marks the document as modified (e.g. recovered autosave)."""
        self._loading = True
        n = self.view.load_marks(marks)
        self.undo.setClean()
        if unsaved:
            self.undo.resetClean()
        self._loading = False
        self._on_marks()
        if unsaved:
            self._autosave()
        skipped = session.count(marks) - n
        if skipped:
            QMessageBox.warning(self, APP_NAME, f"{skipped} saved box(es) were for pages this PDF does not "
                                                "have and were skipped.")
        return n

    def _read_progress(self, path):
        try:
            return session.read(path)
        except ValueError:
            return None

    def _offer_resume(self):
        """After opening a PDF: offer saved progress (next to the PDF) or recovered autosave."""
        doc = self.doc
        candidates = []
        side = session.sidecar_path(doc.path)
        side_info = self._read_progress(side) if os.path.isfile(side) else None
        side_ok = bool(side_info and side_info["pdf"]["sha256"] == doc.fingerprint)
        if side_ok:
            candidates.append(("file", side, side_info))
        auto = session.autosave_file(autosave_dir(), doc.fingerprint)
        auto_info = self._read_progress(auto) if os.path.isfile(auto) else None
        if auto_info and auto_info["marks"]:
            if not (side_ok and _norm(side_info["marks"]) == _norm(auto_info["marks"])):
                candidates.append(("auto", auto, auto_info))
        if not candidates:
            return
        kind, path, info = max(candidates, key=lambda c: c[2]["saved"])
        if self._ask_resume(kind, info):
            self._apply_marks(info["marks"], unsaved=(kind == "auto"))
            if side_ok:
                self.project_path = side
            self.statusBar().showMessage(f"Resumed {session.count(info['marks'])} box(es)", 5000)
        elif kind == "auto":
            self._remove_autosave()

    def _ask_resume(self, kind, info):
        n = session.count(info["marks"])
        if kind == "file":
            text = (f"Saved progress was found for this PDF:\n{n} box(es), saved {_when(info['saved'])}.\n\n"
                    "Continue where you left off?")
            yes, no = "Resume", "Start fresh"
        else:
            text = (f"Unsaved redaction boxes from your last session were recovered:\n{n} box(es), "
                    f"{_when(info['saved'])}.\n\nRestore them?")
            yes, no = "Restore", "Discard"
        box = QMessageBox(QMessageBox.Icon.Question, APP_NAME, text, parent=self)
        yes_btn = box.addButton(yes, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(no, QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(yes_btn)
        box.exec()
        return box.clickedButton() is yes_btn

    def open_progress(self, path):
        """Open a .redactpdf progress file: finds its PDF and restores the boxes."""
        try:
            info = session.read(path)
        except ValueError as e:
            QMessageBox.critical(self, APP_NAME, f"Could not open the progress file:\n{path}\n\n{e}")
            return False
        pdf = info["pdf"]
        options = [pdf["path"], os.path.join(os.path.dirname(os.path.abspath(path)), pdf["name"])]
        found = next((p for p in options if p and os.path.isfile(p)), None)
        if found is None:
            QMessageBox.information(self, APP_NAME, f"The PDF for this progress file was not found:\n"
                                                    f"{pdf['path'] or pdf['name']}\n\nPlease locate it.")
            found, _ = QFileDialog.getOpenFileName(self, f"Locate {pdf['name']}", os.path.dirname(path),
                                                   "PDF files (*.pdf)")
            if not found:
                return False
        doc = self._load_pdf(found)
        if doc is None:
            return False
        if pdf["sha256"] and doc.fingerprint != pdf["sha256"]:
            if QMessageBox.question(
                    self, APP_NAME, f"“{os.path.basename(found)}” is not the same file the progress was saved "
                                    "for (it was changed or replaced). The boxes may not line up.\n\n"
                                    "Load the boxes anyway?") != QMessageBox.StandardButton.Yes:
                return False
        self._show_document(doc)
        auto = session.autosave_file(autosave_dir(), doc.fingerprint)
        auto_info = self._read_progress(auto) if os.path.isfile(auto) else None
        if auto_info and auto_info["marks"] and auto_info["saved"] > info["saved"] \
                and _norm(auto_info["marks"]) != _norm(info["marks"]) and self._ask_resume("auto", auto_info):
            self._apply_marks(auto_info["marks"], unsaved=True)
        else:
            self._apply_marks(info["marks"], unsaved=False)
        self.project_path = path
        self.statusBar().showMessage(f"Loaded {self.view.mark_count()} box(es) from {os.path.basename(path)}", 5000)
        return True

    def save_progress(self, choose=False):
        """Save the boxes (not the redacted PDF) so work can continue later."""
        if not self.doc:
            return False
        path = self.project_path
        if choose or not path:
            default = path or session.sidecar_path(self.doc.path)
            path, _ = QFileDialog.getSaveFileName(self, "Save redaction progress", default, PROGRESS_FILTER)
            if not path:
                return False
            if not path.lower().endswith(session.EXT):
                path += session.EXT
            path = os.path.normpath(path)
        try:
            session.write(path, self.doc.path, self.doc.fingerprint, self.doc.page_count, self.view.marks())
        except OSError as e:
            QMessageBox.critical(self, APP_NAME, f"Could not save progress:\n{path}\n\n{e}")
            return False
        self.project_path = path
        self.undo.setClean()
        self._remove_autosave()
        self.statusBar().showMessage(f"Progress saved: {path}", 6000)
        return True

    def _schedule_autosave(self, *_):
        if self.doc and not self._loading:
            self._autosave_timer.start()

    def _autosave(self):
        """Keep a copy of the current boxes in app data (crash / forgotten-save recovery)."""
        if not self.doc or self._loading:
            return
        marks = self.view.marks()
        path = session.autosave_file(autosave_dir(), self.doc.fingerprint)
        try:
            if marks:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                session.write(path, self.doc.path, self.doc.fingerprint, self.doc.page_count, marks)
            elif os.path.exists(path):
                os.remove(path)
        except OSError:
            pass  # autosave is best effort

    def _remove_autosave(self):
        self._autosave_timer.stop()
        if self.doc:
            try:
                os.remove(session.autosave_file(autosave_dir(), self.doc.fingerprint))
            except OSError:
                pass

    def close_document(self):
        if not self.maybe_save():
            return
        self._autosave_timer.stop()
        self.search.reset()
        self.view.set_document(None)
        self.thumbs.set_document(None)
        self.doc = None
        self.project_path = None
        self._update_actions()
        self._update_title()
        self.marks_label.setText("")

    def export_pdf(self):
        if not self.doc:
            return False
        marks = self.view.marks()
        if not marks and QMessageBox.question(
                self, APP_NAME, "There are no redaction boxes. Save a copy anyway?") != QMessageBox.StandardButton.Yes:
            return False
        dlg = SaveDialog(self, self.settings)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        opts = dlg.options()
        self.settings.setValue("save/flatten", opts["flatten"])
        self.settings.setValue("save/dpi", f"{opts['dpi']} dpi")
        self.settings.setValue("save/scrub", opts["scrub"])

        base, _ = os.path.splitext(self.doc.path)
        path, _ = QFileDialog.getSaveFileName(self, "Save redacted PDF", base + "_redacted.pdf",
                                              "PDF files (*.pdf)")
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(self.doc.path)):
            if QMessageBox.warning(self, APP_NAME, "This will overwrite the original file. The unredacted "
                                                   "version will be lost. Continue?",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                    != QMessageBox.StandardButton.Yes:
                return False

        progress = QProgressDialog("Saving redacted PDF…", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(400)

        def on_progress(done, total):
            progress.setMaximum(total)
            progress.setValue(done)
            QApplication.processEvents()
            return not progress.wasCanceled()

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            leaks = self.doc.save(path, marks, progress=on_progress, **opts)
        except InterruptedError:
            return False
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Saving failed:\n{e}")
            return False
        finally:
            QApplication.restoreOverrideCursor()
            progress.close()

        if self.project_path:  # keep the progress file in step with what was exported
            try:
                session.write(self.project_path, self.doc.path, self.doc.fingerprint, self.doc.page_count, marks)
            except OSError:
                pass
        self._autosave()  # boxes stay recoverable: reopening the PDF offers them again
        self.undo.setClean()
        n = sum(len(v) for v in marks.values())
        if leaks:
            pages = sorted({p + 1 for p, _ in leaks})
            QMessageBox.warning(self, APP_NAME, f"Saved, but text could still be found under {len(leaks)} "
                                                f"box(es) on page(s) {pages}. Try saving with "
                                                "“flatten to images”.")
            return True
        msg = QMessageBox(QMessageBox.Icon.Information, APP_NAME,
                          f"Saved {os.path.basename(path)}\n\n{n} area(s) redacted on {len(marks)} page(s). "
                          "Checked: no text remains under the redacted areas.", parent=self)
        open_btn = msg.addButton("Open file", QMessageBox.ButtonRole.ActionRole)
        folder_btn = msg.addButton("Show folder", QMessageBox.ButtonRole.ActionRole)
        msg.addButton(QMessageBox.StandardButton.Ok)
        msg.exec()
        if msg.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        elif msg.clickedButton() is folder_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
        return True

    # ------------------------------------------------------------------ window events

    def dragEnterEvent(self, e):
        urls = e.mimeData().urls()
        if urls and urls[0].toLocalFile().lower().endswith((".pdf", session.EXT)):
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.open_file(e.mimeData().urls()[0].toLocalFile())

    def closeEvent(self, e):
        if not self.maybe_save():
            e.ignore()
            return
        self._autosave_timer.stop()
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        e.accept()
