from redact_pdf.snapping import Snapper, SNAP_PAD

WORD = (100, 100, 150, 112)
OTHER = (300, 400, 360, 412)


def make():
    return Snapper([WORD, OTHER], 612, 792)


def test_point_snaps_to_nearby_word_edge():
    r = make().snap(97, 104, tol=5)
    assert r.snapped_x and r.x == WORD[0] - SNAP_PAD
    assert r.guides


def test_point_far_from_content_does_not_snap():
    r = make().snap(200, 250, tol=5)
    assert (r.x, r.y, r.snapped_x, r.snapped_y) == (200, 250, False, False)


def test_page_edges_snap():
    r = make().snap(3, 789, tol=5)
    assert (r.x, r.y) == (0, 792)


def test_dragged_corner_snaps_to_word_end_on_same_rows():
    # box from (95, 95) dragged to the right; cursor near the word's right edge
    r = make().snap(153, 116, tol=5, anchor=(95, 95))
    assert r.x == WORD[2] + SNAP_PAD
    assert r.y == WORD[3] + SNAP_PAD


def test_no_x_snap_to_content_outside_the_box_rows():
    # OTHER's left edge is at x=300, but the box spans y 95..116 only
    r = make().snap(302, 116, tol=5, anchor=(95, 95))
    assert r.x == 302


def test_marks_used_for_alignment_without_pad():
    s = Snapper([], 612, 792, marks=[(50, 50, 80, 60)])
    r = s.snap(81, 300, tol=5, anchor=(20, 55))
    assert r.x == 80


def test_move_snaps_nearest_edge():
    dx, dy, guides = make().snap_rect_move((102, 90, 152, 120), tol=5)
    # left edge 102 is 3 from 99 (100 - pad); right edge 152 is 1 from 151 -> right wins
    assert dx == -1
    assert guides
