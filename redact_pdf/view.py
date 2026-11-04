"""The page view: continuous scrolling pages, zoom/pan, drawing and editing redaction boxes."""
import time

from PySide6.QtCore import Qt, QRectF, QPointF, QTimer, Signal, QEvent, QPoint, QSize
from PySide6.QtGui import (QImage, QPixmap, QPainter, QColor, QPen, QBrush, QTransform,
                           QUndoCommand, QCursor, QPalette)
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsRectItem,
                               QGraphicsLineItem, QWidget, QMenu, QApplication)

from .snapping import Snapper
from .document import TEXT_PAD

PAGE_GAP = 18          # points between pages
SNAP_TOL_PX = 9        # snapping distance in screen pixels
HANDLE_PX = 7          # resize grip size in screen pixels
MIN_BOX = 2.0          # smallest redaction box, points
MIN_ZOOM, MAX_ZOOM = 0.1, 16.0
BASE_CAP = 3.0         # max pixel-per-point of whole-page renders; above that a clipped detail render is used
RENDER_BUDGET = 0.06   # seconds of rendering per event-loop turn

BOX, TEXT, PAN = "box", "text", "pan"

MARK_FILL = QColor(0, 0, 0, 185)
MARK_HOVER = QColor(0, 0, 0, 140)
SELECT_COLOR = QColor(255, 140, 0)
DRAFT_FILL = QColor(0, 0, 0, 120)
TEXT_DRAFT_FILL = QColor(30, 120, 255, 90)
GUIDE_COLOR = QColor(0, 160, 255)
HIGHLIGHT_FILL = QColor(255, 210, 0, 110)


def to_pixmap(rendered):
    w, h, stride, samples, gray = rendered
    fmt = QImage.Format.Format_Grayscale8 if gray else QImage.Format.Format_RGB888
    return QPixmap.fromImage(QImage(samples, w, h, stride, fmt).copy())


def rect_tuple(r: QRectF):
    return (r.left(), r.top(), r.right(), r.bottom())


# ------------------------------------------------------------------ scene items

class PageItem(QGraphicsItem):
    def __init__(self, index, w, h):
        super().__init__()
        self.index, self.w, self.h = index, w, h
        self.base = None
        self.base_scale = 0.0
        self.detail = None
        self.detail_rect = None
        self.detail_scale = 0.0

    def boundingRect(self):
        return QRectF(0, 0, self.w, self.h)

    def paint(self, p, option, widget=None):
        r = self.boundingRect()
        p.fillRect(r, Qt.GlobalColor.white)
        if self.base is not None:
            p.drawPixmap(r, self.base, QRectF(self.base.rect()))
        if self.detail is not None:
            p.drawPixmap(self.detail_rect, self.detail, QRectF(self.detail.rect()))
        pen = QPen(QColor(0, 0, 0, 90), 0)
        p.setPen(pen)
        p.drawRect(r)

    def marks(self):
        return [c for c in self.childItems() if isinstance(c, RedactItem)]

    def drop_renders(self):
        self.base, self.base_scale = None, 0.0
        self.detail, self.detail_rect, self.detail_scale = None, None, 0.0


class RedactItem(QGraphicsRectItem):
    def __init__(self, rect: QRectF, view):
        super().__init__(0, 0, rect.width(), rect.height())
        self.view = view
        self.setPos(rect.topLeft())
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self._hover = False
        self._drag = None

    # geometry in page coordinates
    def page_rect(self):
        return QRectF(self.pos(), self.rect().size())

    def set_page_rect(self, r: QRectF):
        r = r.normalized()
        self.prepareGeometryChange()
        self.setPos(r.topLeft())
        self.setRect(0, 0, r.width(), r.height())

    def page(self):
        return self.parentItem()

    def _grip(self):
        r = self.rect()
        g = HANDLE_PX / self.view.zoom
        return min(g, r.width() / 3, r.height() / 3)

    def edges_at(self, local: QPointF):
        r, g = self.rect(), self._grip()
        e = ""
        if local.x() <= g: e += "l"
        elif local.x() >= r.width() - g: e += "r"
        if local.y() <= g: e += "t"
        elif local.y() >= r.height() - g: e += "b"
        return e

    def paint(self, p, option, widget=None):
        r = self.rect()
        if self.view.preview:
            p.fillRect(r, Qt.GlobalColor.black)
            return
        p.fillRect(r, MARK_HOVER if self._hover else MARK_FILL)
        if self.isSelected():
            p.setPen(QPen(SELECT_COLOR, 2 / self.view.zoom))
            p.drawRect(r)
            g = self._grip()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(SELECT_COLOR)
            w, h = r.width(), r.height()
            for x in (0, (w - g) / 2, w - g):
                for y in (0, (h - g) / 2, h - g):
                    if (x, y) != ((w - g) / 2, (h - g) / 2):
                        p.drawRect(QRectF(x, y, g, g))

    # --- interaction (only active with the box tool) ---

    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.unsetCursor()
        self.update()

    def hoverMoveEvent(self, e):
        if self.view.tool != BOX:
            self.unsetCursor()
            return
        cursors = {"l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
                   "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
                   "lt": Qt.CursorShape.SizeFDiagCursor, "rb": Qt.CursorShape.SizeFDiagCursor,
                   "rt": Qt.CursorShape.SizeBDiagCursor, "lb": Qt.CursorShape.SizeBDiagCursor}
        self.setCursor(cursors.get(self.edges_at(e.pos()), Qt.CursorShape.SizeAllCursor))

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            e.ignore()
            return
        if not self.isSelected() and not (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.scene().clearSelection()
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.setSelected(not self.isSelected())
            e.accept()
            return
        self.setSelected(True)
        edges = self.edges_at(e.pos())
        movers = [it for it in self.scene().selectedItems()
                  if isinstance(it, RedactItem) and it.page() is self.page()] if not edges else [self]
        self._drag = dict(edges=edges, start=e.scenePos(),
                          before={it: it.page_rect() for it in movers})
        self.view.begin_item_drag()
        e.accept()

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        self.view.item_drag_move(self, e.scenePos(), e.modifiers())

    def mouseReleaseEvent(self, e):
        if not self._drag:
            return
        self.view.end_item_drag(self)


class GeometryCommand(QUndoCommand):
    def __init__(self, view, changes, text="Move/resize box"):
        super().__init__(text)
        self.view, self.changes = view, changes  # [(item, old, new)]

    def redo(self):
        for item, _old, new in self.changes:
            item.set_page_rect(new)
        self.view.notify_changed({it.page().index for it, _, _ in self.changes})

    def undo(self):
        for item, old, _new in self.changes:
            item.set_page_rect(old)
        self.view.notify_changed({it.page().index for it, _, _ in self.changes})


class AddMarksCommand(QUndoCommand):
    def __init__(self, view, entries, text="Add redaction"):
        super().__init__(text)
        self.view = view
        self.entries = [(page, RedactItem(rect, view)) for page, rect in entries]

    def redo(self):
        for page, item in self.entries:
            item.setParentItem(page)
        self.view.notify_changed({p.index for p, _ in self.entries})

    def undo(self):
        for page, item in self.entries:
            item.setSelected(False)
            self.view.scene().removeItem(item)
        self.view.notify_changed({p.index for p, _ in self.entries})


class RemoveMarksCommand(QUndoCommand):
    def __init__(self, view, items, text="Delete redaction"):
        super().__init__(text)
        self.view = view
        self.entries = [(it.page(), it) for it in items]

    def redo(self):
        for _page, item in self.entries:
            item.setSelected(False)
            self.view.scene().removeItem(item)
        self.view.notify_changed({p.index for p, _ in self.entries})

    def undo(self):
        for page, item in self.entries:
            item.setParentItem(page)
        self.view.notify_changed({p.index for p, _ in self.entries})


# ------------------------------------------------------------------ magnifier

class Loupe(QWidget):
    """Magnified view around the cursor, shown while drawing or resizing boxes."""
    SIZE = 220

    def __init__(self, view):
        super().__init__(view.viewport())
        self.view = view
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
        self._pix = None
        self._state = None

    def show_for(self, page, point, draft=None, guides=()):
        z = self.view.zoom
        scale = min(max(z * 4, 4.0), 32.0)   # pixels per point inside the loupe
        half = self.SIZE / scale / 2
        clip = QRectF(point.x() - half, point.y() - half, 2 * half, 2 * half)
        dpr = self.devicePixelRatioF()
        key = (page.index, round(clip.x() * scale), round(clip.y() * scale), round(scale, 3))
        if self._state is None or self._state[0] != key:
            pix = to_pixmap(self.view.doc.render(page.index, scale * dpr, clip=rect_tuple(clip)))
            pix.setDevicePixelRatio(dpr)
            self._pix = pix
        self._state = (key, page, point, clip, scale, draft, list(guides))
        # keep away from the cursor
        vp = self.view.viewport()
        cur = self.view.mapFromScene(page.mapToScene(point))
        x = vp.width() - self.SIZE - 12 if cur.x() < vp.width() / 2 else 12
        self.move(x, 12)
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, e):
        if not self._state:
            return
        _key, page, point, clip, scale, draft, guides = self._state
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.white)
        p.drawPixmap(0, 0, self._pix)
        p.save()
        p.scale(scale, scale)
        p.translate(-clip.x(), -clip.y())
        for m in page.marks():
            if m is not self.view.active_item():
                p.fillRect(m.page_rect(), MARK_FILL)
        for h in self.view.highlights_for(page.index):
            p.fillRect(h, HIGHLIGHT_FILL)
        if draft is not None:
            for d in (draft if isinstance(draft, list) else [draft]):
                p.fillRect(d, DRAFT_FILL)
                p.setPen(QPen(SELECT_COLOR, 0))
                p.drawRect(d)
        p.setPen(QPen(GUIDE_COLOR, 0, Qt.PenStyle.DashLine))
        for g in guides:
            p.drawLine(QPointF(g[0], g[1]), QPointF(g[2], g[3]))
        p.restore()
        # crosshair
        c = self.SIZE / 2
        p.setPen(QPen(QColor(255, 0, 80), 1))
        p.drawLine(QPointF(c - 12, c), QPointF(c - 3, c))
        p.drawLine(QPointF(c + 3, c), QPointF(c + 12, c))
        p.drawLine(QPointF(c, c - 12), QPointF(c, c - 3))
        p.drawLine(QPointF(c, c + 3), QPointF(c, c + 12))
        p.setPen(QPen(QColor(60, 60, 60), 2))
        p.drawRect(self.rect().adjusted(1, 1, -1, -1))
        p.end()


# ------------------------------------------------------------------ the view

class PdfView(QGraphicsView):
    zoom_changed = Signal(float)
    current_page_changed = Signal(int)
    marks_changed = Signal(set)          # page indices whose boxes changed
    tool_changed = Signal(str)
    status = Signal(str)

    def __init__(self, undo_stack, parent=None):
        super().__init__(parent)
        self.undo_stack = undo_stack
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setBackgroundBrush(QColor(82, 86, 89))
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setMouseTracking(True)
        self.setAcceptDrops(False)
        self.viewport().grabGesture(Qt.GestureType.PinchGesture)

        self.doc = None
        self.pages = []
        self.zoom = 1.0
        self.tool = BOX
        self.snapping = True
        self.preview = False
        self.loupe_enabled = True
        self._current_page = -1
        self._highlights = {}
        self._highlight_items = []

        self._draw = None        # state of a box/text drag in progress
        self._pan = None         # last pan position
        self._space = False
        self._item_drag = None
        self._last_mouse = None
        self._guides = []

        self.loupe = Loupe(self)

        self._render_timer = QTimer(self, singleShot=True, interval=40)
        self._render_timer.timeout.connect(self.update_renders)
        self._scroll_timer = QTimer(self, interval=30)
        self._scroll_timer.timeout.connect(self._auto_scroll)

    # ---------------------------------------------------------- document

    def set_document(self, doc):
        self.cancel_drag()
        self.undo_stack.clear()
        self.scene().clear()
        self._highlight_items = []
        self._highlights = {}
        self._guides = []
        self.doc = doc
        self.pages = []
        if doc is None:
            self.scene().setSceneRect(QRectF())
            return
        y = PAGE_GAP
        max_w = 0
        for i in range(doc.page_count):
            w, h = doc.page_size(i)
            item = PageItem(i, w, h)
            item.setPos(-w / 2, y)
            self.scene().addItem(item)
            self.pages.append(item)
            y += h + PAGE_GAP
            max_w = max(max_w, w)
        self.scene().setSceneRect(QRectF(-max_w / 2 - PAGE_GAP, 0, max_w + 2 * PAGE_GAP, y))
        self._current_page = -1
        self.fit_width()
        self.go_to_page(0)

    def marks(self):
        out = {}
        for page in self.pages:
            rects = [rect_tuple(m.page_rect()) for m in page.marks()]
            if rects:
                out[page.index] = rects
        return out

    def load_marks(self, marks):
        """Place boxes from saved progress ({pno: [rect]}) without creating undo steps.
        Returns the number of boxes placed (boxes on missing pages are skipped)."""
        n, changed = 0, set()
        for pno, rects in marks.items():
            if not 0 <= pno < len(self.pages):
                continue
            page = self.pages[pno]
            for x0, y0, x1, y1 in rects:
                r = QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized().intersected(page.boundingRect())
                if r.width() >= MIN_BOX / 2 and r.height() >= MIN_BOX / 2:
                    RedactItem(r, self).setParentItem(page)
                    n += 1
                    changed.add(pno)
        self.notify_changed(changed)
        return n

    def mark_count(self):
        return sum(len(p.marks()) for p in self.pages)

    def notify_changed(self, pages):
        self.marks_changed.emit(set(pages))
        self.viewport().update()

    def active_item(self):
        return self._item_drag["item"] if self._item_drag else None

    # ---------------------------------------------------------- zoom & navigation

    def set_zoom(self, z, anchor=None):
        z = max(MIN_ZOOM, min(MAX_ZOOM, z))
        if not self.pages or abs(z - self.zoom) < 1e-6 and self.transform().m11() == z:
            return
        if anchor is None:
            anchor = self.viewport().rect().center()
        anchor = QPoint(int(anchor.x()), int(anchor.y()))
        scene_pt = self.mapToScene(anchor)
        self.zoom = z
        self.setTransform(QTransform.fromScale(z, z))
        delta = self.mapFromScene(scene_pt) - anchor
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + delta.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + delta.y())
        self.zoom_changed.emit(z)
        self._viewport_changed()
        if self._draw and self._last_mouse is not None:
            self._drag_update(self._last_mouse)

    def zoom_in(self):
        self.set_zoom(self.zoom * 1.25)

    def zoom_out(self):
        self.set_zoom(self.zoom / 1.25)

    def fit_width(self):
        if not self.pages:
            return
        cur = max(self._current_page, 0)
        w = max(p.w for p in self.pages) + 2 * PAGE_GAP
        self.set_zoom((self.viewport().width() - 4) / w)
        self.go_to_page(cur)

    def fit_page(self):
        if not self.pages:
            return
        cur = max(self._current_page, 0)
        p = self.pages[cur]
        vp = self.viewport()
        self.set_zoom(min((vp.width() - 4) / (p.w + 2 * PAGE_GAP), (vp.height() - 4) / (p.h + PAGE_GAP)))
        self.go_to_page(cur)

    def zoom_to_rect(self, page, rect: QRectF):
        vp = self.viewport()
        r = rect.adjusted(-20, -20, 20, 20)
        self.set_zoom(min(vp.width() / r.width(), vp.height() / r.height()))
        self.centerOn(page.mapToScene(rect.center()))

    def go_to_page(self, index):
        if not self.pages:
            return
        index = max(0, min(index, len(self.pages) - 1))
        item = self.pages[index]
        top = self.mapFromScene(QPointF(item.x() + item.w / 2, item.y() - PAGE_GAP / 2))
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + top.y())
        self._viewport_changed()

    def current_page(self):
        return self._current_page

    def page_at(self, scene_pt):
        for p in self.pages:
            if p.sceneBoundingRect().contains(scene_pt):
                return p
        return None

    def _update_current_page(self):
        if not self.pages:
            return
        vis = self.mapToScene(self.viewport().rect()).boundingRect()
        best, best_h = self._current_page, -1
        for p in self.pages:
            inter = p.sceneBoundingRect().intersected(vis)
            if inter.height() > best_h + 0.5:
                best, best_h = p.index, inter.height()
        if best != self._current_page:
            self._current_page = best
            self.current_page_changed.emit(best)

    def _viewport_changed(self):
        self._update_current_page()
        self._render_timer.start()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._viewport_changed()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._viewport_changed()

    # ---------------------------------------------------------- rendering

    def update_renders(self):
        if not self.doc:
            return
        t0 = time.perf_counter()
        vis = self.mapToScene(self.viewport().rect()).boundingRect()
        keep = vis.adjusted(0, -2 * vis.height(), 0, 2 * vis.height())
        need = self.zoom * self.devicePixelRatioF()
        base_scale = min(need, BASE_CAP)
        pending = False
        # visible pages first, then the prefetch zone
        order = sorted(self.pages, key=lambda p: not p.sceneBoundingRect().intersects(vis))
        for page in order:
            r = page.sceneBoundingRect()
            if not r.intersects(keep):
                if page.base is not None:
                    page.drop_renders()
                continue
            if time.perf_counter() - t0 > RENDER_BUDGET:
                pending = True
                break
            if page.base is None or not (0.9 * base_scale <= page.base_scale <= 1.6 * base_scale):
                page.base = to_pixmap(self.doc.render(page.index, base_scale))
                page.base_scale = base_scale
                page.update()
            if need > BASE_CAP * 1.05 and r.intersects(vis):
                local = page.mapFromScene(vis).boundingRect().intersected(page.boundingRect())
                if (page.detail is None or abs(page.detail_scale - need) > 1e-3
                        or not page.detail_rect.contains(local)):
                    m = 0.3
                    clip = local.adjusted(-local.width() * m, -local.height() * m,
                                          local.width() * m, local.height() * m)
                    clip = clip.intersected(page.boundingRect())
                    page.detail = to_pixmap(self.doc.render(page.index, need, clip=rect_tuple(clip)))
                    page.detail_rect = clip
                    page.detail_scale = need
                    page.update()
            elif page.detail is not None:
                page.detail, page.detail_rect = None, None
                page.update()
        if pending:
            self._render_timer.start(0)

    # ---------------------------------------------------------- highlights (search results)

    def set_highlights(self, results):
        for it in self._highlight_items:
            self.scene().removeItem(it)
        self._highlight_items = []
        self._highlights = {}
        for pno, rects in (results or {}).items():
            page = self.pages[pno]
            self._highlights[pno] = [QRectF(QPointF(r[0], r[1]), QPointF(r[2], r[3])) for r in rects]
            for r in self._highlights[pno]:
                it = QGraphicsRectItem(r, page)
                it.setBrush(HIGHLIGHT_FILL)
                it.setPen(QPen(QColor(230, 160, 0), 0))
                it.setZValue(1)
                it.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                self._highlight_items.append(it)

    def highlights_for(self, pno):
        return self._highlights.get(pno, [])

    def show_rect(self, pno, rect):
        page = self.pages[pno]
        r = QRectF(QPointF(rect[0], rect[1]), QPointF(rect[2], rect[3]))
        self.ensureVisible(page.mapRectToScene(r), 80, 80)

    # ---------------------------------------------------------- tools & editing commands

    def set_tool(self, tool):
        self.cancel_drag()
        self.tool = tool
        self._update_cursor()
        self.tool_changed.emit(tool)

    def _update_cursor(self):
        if self._pan is not None:
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self._space or self.tool == PAN:
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        elif self.tool == TEXT:
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def set_preview(self, on):
        self.preview = on
        self.viewport().update()

    def add_marks(self, entries, text="Add redaction"):
        """entries: [(PageItem, QRectF)]"""
        entries = [(p, r.normalized().intersected(p.boundingRect())) for p, r in entries]
        entries = [(p, r) for p, r in entries if r.width() >= MIN_BOX and r.height() >= MIN_BOX]
        if entries:
            self.undo_stack.push(AddMarksCommand(self, entries, text))
        return len(entries)

    def add_marks_by_index(self, results, text):
        entries = []
        for pno, rects in results.items():
            page = self.pages[pno]
            existing = {tuple(round(v, 1) for v in rect_tuple(m.page_rect())) for m in page.marks()}
            for r in rects:
                if tuple(round(v, 1) for v in r) not in existing:
                    entries.append((page, QRectF(QPointF(r[0], r[1]), QPointF(r[2], r[3]))))
        return self.add_marks(entries, text)

    def selected_marks(self):
        return [it for it in self.scene().selectedItems() if isinstance(it, RedactItem)]

    def delete_selected(self):
        items = self.selected_marks()
        if items:
            self.undo_stack.push(RemoveMarksCommand(self, items,
                                                    f"Delete {len(items)} box(es)"))

    def select_all_on_page(self):
        if 0 <= self._current_page < len(self.pages):
            self.scene().clearSelection()
            for m in self.pages[self._current_page].marks():
                m.setSelected(True)

    def clear_page(self):
        if 0 <= self._current_page < len(self.pages):
            items = self.pages[self._current_page].marks()
            if items:
                self.undo_stack.push(RemoveMarksCommand(self, items, "Clear page"))

    def clear_all(self):
        items = [m for p in self.pages for m in p.marks()]
        if items:
            self.undo_stack.push(RemoveMarksCommand(self, items, "Clear all"))

    def copy_to_all_pages(self, items=None):
        items = items or self.selected_marks()
        entries = []
        for it in items:
            r = it.page_rect()
            for page in self.pages:
                if page is it.page():
                    continue
                if not any(m.page_rect() == r for m in page.marks()):
                    entries.append((page, QRectF(r)))
        return self.add_marks(entries, "Copy box to all pages")

    def nudge(self, dx, dy):
        items = self.selected_marks()
        if not items:
            return False
        changes = []
        for it in items:
            old = it.page_rect()
            new = self._clamp_rect(old.translated(dx, dy), it.page())
            changes.append((it, old, new))
        self.undo_stack.push(GeometryCommand(self, changes, "Nudge box"))
        return True

    @staticmethod
    def _clamp_rect(r, page):
        x = min(max(r.x(), 0), page.w - r.width())
        y = min(max(r.y(), 0), page.h - r.height())
        return QRectF(x, y, r.width(), r.height())

    # ---------------------------------------------------------- snapping helpers

    def _snapper(self, page, exclude=()):
        marks = [rect_tuple(m.page_rect()) for m in page.marks() if m not in exclude]
        return Snapper(self.doc.content_boxes(page.index), page.w, page.h, marks)

    def _snap_enabled(self, modifiers):
        return self.snapping and not (modifiers & Qt.KeyboardModifier.AltModifier)

    def _set_guides(self, page, guides):
        for g in self._guides:
            self.scene().removeItem(g)
        self._guides = []
        pen = QPen(GUIDE_COLOR, 0, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        for x0, y0, x1, y1 in guides:
            line = QGraphicsLineItem(x0, y0, x1, y1, page)
            line.setPen(pen)
            line.setZValue(5)
            self._guides.append(line)

    @staticmethod
    def _clamp_point(page, pt):
        return QPointF(min(max(pt.x(), 0), page.w), min(max(pt.y(), 0), page.h))

    # ---------------------------------------------------------- dragging existing boxes

    def begin_item_drag(self):
        pass  # state lives in the item; hook kept for symmetry

    def item_drag_move(self, item, scene_pos, modifiers):
        d = item._drag
        page = item.page()
        tol = SNAP_TOL_PX / self.zoom
        snap = self._snap_enabled(modifiers)
        self._item_drag = dict(item=item)
        guides = []
        if d["edges"]:
            old = d["before"][item]
            pt = self._clamp_point(page, page.mapFromScene(scene_pos))
            edges = d["edges"]
            horiz = "l" in edges or "r" in edges
            vert = "t" in edges or "b" in edges
            # (ax, ay) is the fixed corner; (x, y) the corner being dragged.
            # On an axis that is not being resized both edges stay where they were.
            ax = old.right() if "l" in edges else old.left()
            ay = old.bottom() if "t" in edges else old.top()
            x = pt.x() if horiz else old.right()
            y = pt.y() if vert else old.bottom()
            if snap:
                res = self._snapper(page, exclude=[item]).snap(x, y, tol, anchor=(ax, ay))
                if horiz:
                    x = res.x
                if vert:
                    y = res.y
                guides = [g for g in res.guides
                          if (g[0] == g[2] and horiz) or (g[1] == g[3] and vert)]
            new = QRectF(QPointF(ax, ay), QPointF(x, y)).normalized()
            if new.width() >= MIN_BOX and new.height() >= MIN_BOX:
                item.set_page_rect(new)
            loupe_pt = QPointF(x, y)
        else:
            delta = scene_pos - d["start"]
            lead = d["before"][item]
            moved = self._clamp_rect(lead.translated(delta), page)
            if snap:
                dx, dy, guides = self._snapper(page, exclude=list(d["before"])).snap_rect_move(
                    rect_tuple(moved), tol)
                moved = self._clamp_rect(moved.translated(dx, dy), page)
            shift = moved.topLeft() - lead.topLeft()
            for it, before in d["before"].items():
                it.set_page_rect(self._clamp_rect(before.translated(shift), page))
            loupe_pt = page.mapFromScene(scene_pos)
        self._set_guides(page, guides)
        if self.loupe_enabled and d["edges"]:
            self.loupe.show_for(page, loupe_pt, draft=item.page_rect(), guides=guides)

    def end_item_drag(self, item):
        d = item._drag
        item._drag = None
        self._item_drag = None
        self._set_guides(None, [])
        self.loupe.hide()
        changes = [(it, before, it.page_rect()) for it, before in d["before"].items()
                   if it.page_rect() != before]
        if changes:
            text = "Resize box" if d["edges"] else "Move box"
            self.undo_stack.push(GeometryCommand(self, changes, text))

    # ---------------------------------------------------------- drawing new boxes / text selection

    def _start_draw(self, scene_pos, modifiers):
        page = self.page_at(scene_pos)
        if page is None:
            return False
        pt = self._clamp_point(page, page.mapFromScene(scene_pos))
        snapper = self._snapper(page)
        if self.tool == BOX and self._snap_enabled(modifiers):
            res = snapper.snap(pt.x(), pt.y(), SNAP_TOL_PX / self.zoom)
            pt = QPointF(res.x, res.y)
        self._draw = dict(page=page, anchor=pt, snapper=snapper, drafts=[], rects=[],
                          press_view=self.mapFromScene(scene_pos), moved=False)
        return True

    def _drafts(self, rects, fill):
        d = self._draw
        while len(d["drafts"]) < len(rects):
            it = QGraphicsRectItem(d["page"])
            it.setZValue(4)
            it.setBrush(fill)
            pen = QPen(SELECT_COLOR, 0)
            it.setPen(pen)
            d["drafts"].append(it)
        while len(d["drafts"]) > len(rects):
            self.scene().removeItem(d["drafts"].pop())
        for it, r in zip(d["drafts"], rects):
            it.setRect(r)
        d["rects"] = rects

    def _drag_update(self, view_pos):
        d = self._draw
        if not d:
            return
        page = d["page"]
        if (view_pos - d["press_view"]).manhattanLength() > 3:
            d["moved"] = True
        pt = self._clamp_point(page, page.mapFromScene(self.mapToScene(view_pos)))
        guides = []
        if self.tool == BOX:
            if self._snap_enabled(QApplication.keyboardModifiers()):
                a = d["anchor"]
                res = d["snapper"].snap(pt.x(), pt.y(), SNAP_TOL_PX / self.zoom, anchor=(a.x(), a.y()))
                pt = QPointF(res.x, res.y)
                guides = res.guides
            rect = QRectF(d["anchor"], pt).normalized()
            self._drafts([rect], DRAFT_FILL)
            self._set_guides(page, guides)
            if self.loupe_enabled:
                self.loupe.show_for(page, pt, draft=rect, guides=guides)
            self.status.emit(f"{rect.width():.1f} × {rect.height():.1f} pt")
        else:
            a = d["anchor"]
            tuples = self.doc.select_text(page.index, (a.x(), a.y()), (pt.x(), pt.y())) if d["moved"] else []
            rects = [QRectF(QPointF(r[0], r[1]), QPointF(r[2], r[3])) for r in tuples]
            self._drafts(rects, TEXT_DRAFT_FILL)
            if self.loupe_enabled and d["moved"]:
                self.loupe.show_for(page, pt, draft=rects)

    def _finish_draw(self):
        d = self._draw
        rects = list(d["rects"]) if d["moved"] else []
        page = d["page"]
        self.cancel_drag()
        if rects:
            n = self.add_marks([(page, r) for r in rects],
                               "Redact text" if self.tool == TEXT else "Add box")
            if n:
                self.status.emit(f"Added {n} box(es)")
        else:
            self.scene().clearSelection()

    def cancel_drag(self):
        d = self._draw
        if d:
            for it in d["drafts"]:
                self.scene().removeItem(it)
        self._draw = None
        self._scroll_timer.stop()
        if self._guides:
            self._set_guides(None, [])
        self.loupe.hide()

    def _auto_scroll(self):
        if not self._draw and not self._item_drag:
            self._scroll_timer.stop()
            return
        pos = self.viewport().mapFromGlobal(QCursor.pos())
        vp = self.viewport().rect()
        m, dx, dy = 24, 0, 0
        if pos.x() < m: dx = -(m - pos.x())
        elif pos.x() > vp.width() - m: dx = pos.x() - (vp.width() - m)
        if pos.y() < m: dy = -(m - pos.y())
        elif pos.y() > vp.height() - m: dy = pos.y() - (vp.height() - m)
        if dx or dy:
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + dx)
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + dy)
            if self._draw:
                self._drag_update(pos)

    # ---------------------------------------------------------- mouse / keyboard / wheel

    def _item_under(self, view_pos):
        for it in self.items(view_pos):
            if isinstance(it, RedactItem):
                return it
        return None

    def mousePressEvent(self, e):
        self.setFocus()
        pos = e.position().toPoint()
        self._last_mouse = pos
        if e.button() == Qt.MouseButton.MiddleButton or (
                e.button() == Qt.MouseButton.LeftButton and (self._space or self.tool == PAN)):
            self._pan = pos
            self._update_cursor()
            e.accept()
            return
        if e.button() != Qt.MouseButton.LeftButton or not self.doc:
            super().mousePressEvent(e)
            return
        if self.tool == BOX and self._item_under(pos) is not None:
            super().mousePressEvent(e)  # the item handles select / move / resize
            self._scroll_timer.start()
            return
        if self._start_draw(self.mapToScene(pos), e.modifiers()):
            self._scroll_timer.start()
            e.accept()
        else:
            self.scene().clearSelection()

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        self._last_mouse = pos
        if self._pan is not None:
            delta = pos - self._pan
            self._pan = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            return
        if self._draw:
            self._drag_update(pos)
            return
        super().mouseMoveEvent(e)
        if self.doc and not self._item_drag:
            page = self.page_at(self.mapToScene(pos))
            if page is not None:
                pt = page.mapFromScene(self.mapToScene(pos))
                self.status.emit(f"Page {page.index + 1}  ·  x {pt.x():.0f}, y {pt.y():.0f} pt")

    def mouseReleaseEvent(self, e):
        if self._pan is not None and e.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self._pan = None
            self._update_cursor()
            return
        if self._draw and e.button() == Qt.MouseButton.LeftButton:
            self._drag_update(e.position().toPoint())
            self._finish_draw()
            return
        self._scroll_timer.stop()
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.doc and self.tool in (BOX, TEXT) \
                and self._item_under(e.position().toPoint()) is None:
            scene_pt = self.mapToScene(e.position().toPoint())
            page = self.page_at(scene_pt)
            if page is not None:
                pt = page.mapFromScene(scene_pt)
                self.cancel_drag()
                word = self.doc.word_at(page.index, pt.x(), pt.y())
                if word:
                    r = word.rect
                    self.add_marks([(page, QRectF(QPointF(r[0] - TEXT_PAD, r[1] - TEXT_PAD),
                                                  QPointF(r[2] + TEXT_PAD, r[3] + TEXT_PAD)))],
                                   "Redact word")
                    self.status.emit(f"Redacted word “{word.text}”")
                return
        super().mouseDoubleClickEvent(e)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = e.angleDelta().y() or e.pixelDelta().y()
            self.set_zoom(self.zoom * (1.0018 ** delta), e.position())
            e.accept()
            return
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - (e.angleDelta().y() or e.angleDelta().x()))
            e.accept()
            return
        super().wheelEvent(e)
        if self._draw:
            self._drag_update(e.position().toPoint())

    def viewportEvent(self, e):
        if e.type() == QEvent.Type.NativeGesture and e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self.set_zoom(self.zoom * (1 + e.value()), e.position())
            return True
        if e.type() == QEvent.Type.Gesture:
            pinch = e.gesture(Qt.GestureType.PinchGesture)
            if pinch is not None:
                f = pinch.scaleFactor()
                if f > 0:
                    self.set_zoom(self.zoom * f, self.viewport().mapFromGlobal(pinch.centerPoint().toPoint()))
                return True
        return super().viewportEvent(e)

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = True
            self._update_cursor()
            return
        if k == Qt.Key.Key_Escape:
            if self._draw:
                self.cancel_drag()
            else:
                self.scene().clearSelection()
            return
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            return
        step = 10.0 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        arrows = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                  Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}
        if k in arrows and self.nudge(*arrows[k]):
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = False
            self._update_cursor()
            return
        super().keyReleaseEvent(e)

    def contextMenuEvent(self, e):
        item = self._item_under(e.pos())
        menu = QMenu(self)
        if item is not None:
            if not item.isSelected():
                self.scene().clearSelection()
                item.setSelected(True)
            n = len(self.selected_marks())
            menu.addAction(f"Delete ({n})" if n > 1 else "Delete", self.delete_selected)
            menu.addAction("Copy to all pages", self.copy_to_all_pages)
            menu.addAction("Zoom to box", lambda: self.zoom_to_rect(item.page(), item.page_rect()))
        else:
            menu.addAction("Select all boxes on this page", self.select_all_on_page)
            menu.addAction("Fit width", self.fit_width)
            menu.addAction("Fit page", self.fit_page)
        menu.exec(e.globalPos())
