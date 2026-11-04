"""Snapping of redaction-box edges to page content (pure logic, no Qt)."""
from dataclasses import dataclass, field

import numpy as np

# gap (points) left between snapped box edge and the content it snaps to, so text is fully covered
SNAP_PAD = 1.0


@dataclass
class SnapResult:
    x: float
    y: float
    snapped_x: bool = False
    snapped_y: bool = False
    guides: list = field(default_factory=list)  # [(x0, y0, x1, y1)] lines to draw


class Snapper:
    """Snaps points / moving box corners to edges of nearby content boxes.

    content: boxes (x0, y0, x1, y1) of text, images, drawings - edges get SNAP_PAD outward.
    marks: other redaction boxes - edges used as-is, for alignment.
    """

    def __init__(self, content, page_w, page_h, marks=()):
        self.page_w, self.page_h = page_w, page_h
        c = np.asarray(content, dtype=float).reshape(-1, 4)
        m = np.asarray(marks, dtype=float).reshape(-1, 4)
        p = SNAP_PAD
        # each box contributes two candidate values per axis
        self._boxes = np.vstack([c, m]) if len(m) else c
        pad = np.concatenate([np.full(len(c), p), np.zeros(len(m))])
        self._lo_x = self._boxes[:, 0] - pad
        self._hi_x = self._boxes[:, 2] + pad
        self._lo_y = self._boxes[:, 1] - pad
        self._hi_y = self._boxes[:, 3] + pad

    def _best(self, value, lo, hi, mask, tol, page_max):
        """Nearest candidate within tol. Returns (value, index_or_-1, snapped)."""
        best_v, best_d, best_i = value, tol, None
        for cand in (lo, hi):
            if mask.any():
                cands = cand[mask]
                d = np.abs(cands - value)
                k = int(np.argmin(d))
                if d[k] <= best_d:
                    best_d, best_v = d[k], float(cands[k])
                    best_i = int(np.flatnonzero(mask)[k])
        for edge in (0.0, page_max):
            if abs(edge - value) <= best_d:
                best_d, best_v, best_i = abs(edge - value), edge, -1
        return best_v, best_i

    def snap(self, x, y, tol, anchor=None):
        """Snap the point (x, y).

        Without `anchor`: snap to content near the point.
        With `anchor` (the fixed opposite corner of a box being drawn/resized): x snaps to
        content that vertically overlaps the box, y to content that horizontally overlaps it.
        """
        b = self._boxes
        if anchor is None:
            y_lo, y_hi, x_lo, x_hi = y, y, x, x
        else:
            y_lo, y_hi = sorted((anchor[1], y))
            x_lo, x_hi = sorted((anchor[0], x))
        x_mask = (b[:, 1] <= y_hi + tol) & (b[:, 3] >= y_lo - tol)
        y_mask = (b[:, 0] <= x_hi + tol) & (b[:, 2] >= x_lo - tol)
        if anchor is None:
            # for a single point, only consider content that is actually close
            x_mask &= (b[:, 0] - tol <= x) & (b[:, 2] + tol >= x)
            y_mask &= (b[:, 1] - tol <= y) & (b[:, 3] + tol >= y)

        sx, ix = self._best(x, self._lo_x, self._hi_x, x_mask, tol, self.page_w)
        sy, iy = self._best(y, self._lo_y, self._hi_y, y_mask, tol, self.page_h)
        res = SnapResult(sx, sy, ix is not None, iy is not None)

        ay = anchor[1] if anchor else sy
        ax = anchor[0] if anchor else sx
        if ix is not None:
            ys = [ay, sy] + ([b[ix, 1], b[ix, 3]] if ix >= 0 else [])
            res.guides.append((sx, min(ys), sx, max(ys)))
        if iy is not None:
            xs = [ax, sx] + ([b[iy, 0], b[iy, 2]] if iy >= 0 else [])
            res.guides.append((min(xs), sy, max(xs), sy))
        return res

    def snap_rect_move(self, rect, tol):
        """Snap a box being moved: shift it so its nearest edge lands on content.
        Returns (dx, dy, guides)."""
        x0, y0, x1, y1 = rect
        b = self._boxes
        x_mask = (b[:, 1] <= y1 + tol) & (b[:, 3] >= y0 - tol)
        y_mask = (b[:, 0] <= x1 + tol) & (b[:, 2] >= x0 - tol)
        best = {}
        for axis, edges, lo, hi, mask, pmax in (
                ("x", (x0, x1), self._lo_x, self._hi_x, x_mask, self.page_w),
                ("y", (y0, y1), self._lo_y, self._hi_y, y_mask, self.page_h)):
            choice = (0.0, None, None)
            best_d = tol
            for e in edges:
                v, i = self._best(e, lo, hi, mask, tol, pmax)
                if i is not None and abs(v - e) <= best_d:
                    best_d, choice = abs(v - e), (v - e, v, i)
            best[axis] = choice
        dx, vx, ix = best["x"]
        dy, vy, iy = best["y"]
        guides = []
        if ix is not None:
            guides.append((vx, y0 + dy, vx, y1 + dy))
        if iy is not None:
            guides.append((x0 + dx, vy, x1 + dx, vy))
        return dx, dy, guides
