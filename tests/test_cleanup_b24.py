"""B2.4 operations added after the first cleanup pass: close_silhouette and
thin_lines.

`snap_grid` and `remove_doubles` were implemented here too, for completeness
against the plan's B2.4 table, and then never wired to anything -- no production
caller, only these tests. `snap_grid` also duplicated `grid.snap_to_grid`, which
IS in the conform path. That is the exact shape of the bug that had just been
found in `import_reference`: a plausible second implementation sitting unwired,
waiting to drift from the one that runs. Deleted rather than kept warm.
"""

import numpy as np
import pytest

from aseprite_mcp.cleanup import OPERATIONS, close_silhouette, run_pipeline, thin_lines


def test_close_silhouette_fills_a_pinhole_with_its_own_ring_colour():
    idx = np.full((8, 8), 2, dtype=np.int32)
    idx[4, 4] = 0  # a hole punched through a solid shape
    out, changed = close_silhouette(idx)
    assert out[4, 4] == 2
    assert changed == 1


def test_close_silhouette_shaves_a_pixel_hanging_by_one_corner():
    """Only genuinely isolated tips. Morphological opening removed anything thin,
    which ate a hand-drawn sprite's 1px legs -- so the rule is now "at most one
    opaque neighbour in 8-connectivity", which a limb never satisfies."""
    idx = np.zeros((8, 8), dtype=np.int32)
    idx[2:6, 2:6] = 3
    idx[1, 1] = 3  # touches the body only at the (2,2) corner
    out, _ = close_silhouette(idx)
    assert out[1, 1] == 0
    assert (out[2:6, 2:6] == 3).all()  # the body itself is untouched


def test_close_silhouette_keeps_a_bump_attached_along_an_edge():
    """A 1px bump sitting on a flat edge has three neighbours. At pixel-art scale
    that is usually deliberate -- a horn, a rivet, a stud -- so it stays."""
    idx = np.zeros((8, 8), dtype=np.int32)
    idx[2:6, 2:6] = 3
    idx[1, 3] = 3
    out, changed = close_silhouette(idx)
    assert out[1, 3] == 3 and changed == 0


def test_close_silhouette_is_a_noop_on_a_clean_shape():
    idx = np.zeros((10, 10), dtype=np.int32)
    idx[3:7, 3:7] = 5
    out, changed = close_silhouette(idx)
    assert changed == 0
    assert np.array_equal(out, idx)


def test_thin_lines_collapses_a_2px_line_to_1px():
    idx = np.full((8, 10), 2, dtype=np.int32)   # opaque field to thin into
    idx[3, 2:8] = 4
    idx[4, 2:8] = 4  # a 2px-thick horizontal line
    out, changed = thin_lines(idx)
    assert changed > 0
    # one of the two rows survives at full length, the run is no longer 2 wide
    assert (out == 4).sum() < (idx == 4).sum()


def test_thin_lines_will_not_erode_a_line_on_the_silhouette_edge():
    """A 2px line with only transparency beside it has nothing to thin into.
    Eroding it would shrink the subject, which is close_silhouette's job."""
    idx = np.zeros((8, 10), dtype=np.int32)
    idx[3, 2:8] = 4
    idx[4, 2:8] = 4
    out, changed = thin_lines(idx)
    assert changed == 0
    assert np.array_equal(out, idx)


def test_thin_lines_leaves_a_filled_region_alone():
    """A 2px-wide detail inside a big filled shape is real art, not a stray
    double line — the distance transform is what tells them apart."""
    idx = np.zeros((16, 16), dtype=np.int32)
    idx[4:12, 4:12] = 7
    out, changed = thin_lines(idx)
    assert changed == 0
    assert np.array_equal(out, idx)


def test_new_ops_are_reachable_from_the_pipeline_and_report_counts():
    assert "close_silhouette" in OPERATIONS and "thin_lines" in OPERATIONS
    idx = np.full((8, 8), 2, dtype=np.int32)
    idx[4, 4] = 0
    out, report = run_pipeline(idx, ["#000000", "#ff0000", "#00ff00"], ["close_silhouette"], 0.5)
    assert report["close_silhouette"] == 1
    assert out[4, 4] == 2


def test_pipeline_preserves_shape_for_every_listed_operation():
    """The cleanup tool diffs input against output pixel for pixel, so no
    operation may resize."""
    rng = np.random.default_rng(0)
    idx = rng.integers(0, 4, size=(16, 16)).astype(np.int32)
    out, _ = run_pipeline(idx, ["#000000", "#ff0000", "#00ff00", "#0000ff"], list(OPERATIONS), 0.5)
    assert out.shape == idx.shape


def test_thin_lines_never_punches_holes_in_the_interior_of_a_shape():
    """The dropped pixel takes its neighbour's colour, not transparency. Setting
    it transparent speckled 1297px of holes across a photo in the B6 gate --
    thinning a line inside a filled region must not make the region see-through."""
    idx = np.full((12, 12), 5, dtype=np.int32)   # solid opaque field
    idx[5:7, 2:10] = 4                            # a 2px line drawn across it
    out, changed = thin_lines(idx)
    assert changed > 0, "the line should still thin"
    assert (out != 0).all(), "no pixel may become transparent"


def test_close_silhouette_does_not_erode_a_shape_that_fills_the_canvas():
    """Morphology at the array edge: the closing step's erosion treats outside
    as background and eats inward from all four sides. On a fully opaque 4x4 it
    punched a 2x2 hole straight through the middle."""
    idx = np.full((4, 4), 1, dtype=np.int32)
    out, changed = close_silhouette(idx)
    assert changed == 0, f"eroded a solid canvas: {out}"
    assert (out == 1).all()


def test_close_silhouette_keeps_a_shape_running_to_the_edge():
    idx = np.zeros((10, 10), dtype=np.int32)
    idx[:, 0:5] = 2           # a block flush against the left edge
    out, _ = close_silhouette(idx)
    assert (out[:, 0:5] == 2).all(), "shaved the edge-flush side"
