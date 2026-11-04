"""Icons drawn with QPainter, so the app needs no image files."""
import struct

from PySide6.QtCore import Qt, QRectF, QPointF, QBuffer, QIODevice
from PySide6.QtGui import QIcon, QImage, QPainter, QPen, QColor, QPainterPath, QPixmap, QPalette, QFont
from PySide6.QtWidgets import QApplication


def _draw(name, p, c):
    """Draw icon `name` on a 24x24 canvas with colour c."""
    pen = QPen(c, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if name == "open":
        path = QPainterPath()
        path.moveTo(3, 19); path.lineTo(3, 6); path.lineTo(9, 6); path.lineTo(11, 8); path.lineTo(19, 8)
        path.lineTo(19, 11)
        p.drawPath(path)
        path = QPainterPath()
        path.moveTo(3, 19); path.lineTo(6.5, 11); path.lineTo(22, 11); path.lineTo(18.5, 19); path.closeSubpath()
        p.drawPath(path)
    elif name == "save":
        p.drawRoundedRect(QRectF(4, 4, 16, 16), 2, 2)
        p.drawRect(QRectF(8, 4, 8, 5))
        p.fillRect(QRectF(7, 13, 10, 5), c)
    elif name in ("undo", "redo"):
        if name == "redo":
            p.translate(24, 0); p.scale(-1, 1)
        path = QPainterPath()
        path.moveTo(5, 10); path.lineTo(15, 10)
        path.cubicTo(22, 10, 22, 20, 15, 20); path.lineTo(10, 20)
        p.drawPath(path)
        p.drawLine(QPointF(5, 10), QPointF(9, 6)); p.drawLine(QPointF(5, 10), QPointF(9, 14))
    elif name == "box":
        p.setPen(QPen(c, 1.5, Qt.PenStyle.DashLine))
        p.drawRect(QRectF(3, 5, 18, 14))
        p.fillRect(QRectF(6, 8, 12, 8), c)
    elif name == "text":
        p.drawLine(QPointF(5, 5), QPointF(19, 5)); p.drawLine(QPointF(12, 5), QPointF(12, 19))
        p.fillRect(QRectF(3, 20, 18, 2.5), c)
    elif name == "pan":
        for a, b, d1, d2 in ((12, 2, (9, 5), (15, 5)), (12, 22, (9, 19), (15, 19))):
            p.drawLine(QPointF(12, 12), QPointF(a, b))
            p.drawLine(QPointF(a, b), QPointF(*d1)); p.drawLine(QPointF(a, b), QPointF(*d2))
        for a, b, d1, d2 in ((2, 12, (5, 9), (5, 15)), (22, 12, (19, 9), (19, 15))):
            p.drawLine(QPointF(12, 12), QPointF(a, b))
            p.drawLine(QPointF(a, b), QPointF(*d1)); p.drawLine(QPointF(a, b), QPointF(*d2))
    elif name == "snap":
        p.setPen(QPen(c, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        path = QPainterPath()
        path.moveTo(6, 4); path.lineTo(6, 12); path.arcTo(QRectF(6, 6, 12, 12), 180, 180); path.lineTo(18, 4)
        p.drawPath(path)
        p.setPen(QPen(QColor(230, 60, 60), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.drawLine(QPointF(6, 3), QPointF(6, 7)); p.drawLine(QPointF(18, 3), QPointF(18, 7))
    elif name in ("loupe", "zoom_in", "zoom_out", "search"):
        p.drawEllipse(QRectF(3, 3, 13, 13))
        p.setPen(QPen(c, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(14.5, 14.5), QPointF(20.5, 20.5))
        p.setPen(pen)
        if name in ("zoom_in", "zoom_out"):
            p.drawLine(QPointF(6.5, 9.5), QPointF(12.5, 9.5))
        if name == "zoom_in":
            p.drawLine(QPointF(9.5, 6.5), QPointF(9.5, 12.5))
        if name == "loupe":
            p.drawLine(QPointF(9.5, 6), QPointF(9.5, 13)); p.drawLine(QPointF(6, 9.5), QPointF(13, 9.5))
            p.drawEllipse(QRectF(7.5, 7.5, 4, 4))
    elif name == "preview":
        path = QPainterPath()
        path.moveTo(2, 12); path.quadTo(12, 2, 22, 12); path.quadTo(12, 22, 2, 12)
        p.drawPath(path)
        p.setBrush(c)
        p.drawEllipse(QRectF(9, 9, 6, 6))
    elif name == "fit_width":
        p.drawRect(QRectF(3, 4, 18, 16))
        p.drawLine(QPointF(6, 12), QPointF(18, 12))
        p.drawLine(QPointF(6, 12), QPointF(9, 9)); p.drawLine(QPointF(6, 12), QPointF(9, 15))
        p.drawLine(QPointF(18, 12), QPointF(15, 9)); p.drawLine(QPointF(18, 12), QPointF(15, 15))
    elif name == "fit_page":
        p.drawRect(QRectF(6, 3, 12, 18))
        for x, y, dx, dy in ((3, 3, 1, 1), (21, 3, -1, 1), (3, 21, 1, -1), (21, 21, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + 3 * dx, y)); p.drawLine(QPointF(x, y), QPointF(x, y + 3 * dy))
    elif name == "export":
        path = QPainterPath()
        path.moveTo(14, 4); path.lineTo(5, 4); path.lineTo(5, 20); path.lineTo(17, 20); path.lineTo(17, 15)
        p.drawPath(path)
        p.fillRect(QRectF(7.5, 7, 6, 2), c); p.fillRect(QRectF(7.5, 11, 4, 2), c)
        p.drawLine(QPointF(11, 13), QPointF(21, 6))
        p.drawLine(QPointF(21, 6), QPointF(16, 6)); p.drawLine(QPointF(21, 6), QPointF(20, 11))
    elif name in ("prev", "next"):
        if name == "next":
            p.translate(0, 24); p.scale(1, -1)
        p.drawLine(QPointF(6, 15), QPointF(12, 9)); p.drawLine(QPointF(12, 9), QPointF(18, 15))


def icon(name):
    pal = QApplication.palette()
    ic = QIcon()
    for mode, role in ((QIcon.Mode.Normal, QPalette.ColorRole.WindowText),
                       (QIcon.Mode.Disabled, None)):
        c = pal.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText) if role is None \
            else pal.color(role)
        for size in (16, 24, 32, 48):
            pm = QPixmap(size, size)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.scale(size / 24, size / 24)
            _draw(name, p, c)
            p.end()
            ic.addPixmap(pm, mode)
    return ic


def app_icon_image(size):
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64, size / 64)
    page = QPainterPath()
    page.moveTo(12, 4); page.lineTo(42, 4); page.lineTo(54, 16); page.lineTo(54, 60); page.lineTo(12, 60)
    page.closeSubpath()
    p.setPen(QPen(QColor(60, 64, 72), 2.5))
    p.setBrush(QColor(255, 255, 255))
    p.drawPath(page)
    fold = QPainterPath()
    fold.moveTo(42, 4); fold.lineTo(42, 16); fold.lineTo(54, 16)
    p.setBrush(QColor(220, 224, 230))
    p.drawPath(fold)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(150, 155, 165))
    for y in (24, 40, 50):
        p.drawRoundedRect(QRectF(18, y, 30, 3.5), 1.5, 1.5)
    p.setBrush(QColor(20, 20, 20))
    p.drawRoundedRect(QRectF(16, 30, 33, 7), 1.5, 1.5)
    p.drawRoundedRect(QRectF(16, 45, 20, 4), 1.5, 1.5)
    p.end()
    return img


def app_icon():
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(QPixmap.fromImage(app_icon_image(s)))
    return ic


def write_ico(path, sizes=(16, 24, 32, 48, 64, 128, 256)):
    """Write a Windows .ico (PNG-compressed entries)."""
    blobs = []
    for s in sizes:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        app_icon_image(s).save(buf, "PNG")
        blobs.append(bytes(buf.data()))
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, data in zip(sizes, blobs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    with open(path, "wb") as f:
        f.write(header + entries + b"".join(blobs))
