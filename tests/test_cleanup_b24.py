"""B2.4 operations added after the first cleanup pass: close_silhouette,
thin_lines, remove_doubles, snap_grid."""

import numpy as np
import pytest

from aseprite_mcp.cleanup import (
    OPERATIONS,
    close_silhouette,
    remove_doubles,
    run_pipeline,
    snap_grid,
    thin_lines,
)
from aseprite_mcp.errors import ToolError


def test_close_silhouette_fills_a_pinhole_with_its_own_ring_colour():
    idx = np.full((8, 8), 2, dtype=np.int32)
    idx[4, 4] = 0  # a hole punched through a solid shape
    out, changed = close_silhouette(idx)
    assert out[4, 4] == 2
    assert changed == 1


def test_close_silhouette_shaves_a_single_pixel_whisker():
    idx = np.zeros((8, 8), dtype=np.int32)
    idx[2:6, 2:6] = 3
    idx[1, 3] = 3  # a 1px protrusion off the top edge
    out, _ = close_silhouette(idx)
    assert out[1, 3] == 0
    assert (out[2:6, 2:6] == 3).all()  # the body itself is untouched


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


def test_remove_doubles_collapses_duplicated_rows_and_columns():
    base = np.array([[1, 2], [3, 4]], dtype=np.int32)
    doubled = np.repeat(np.repeat(base, 2, axis=0), 2, axis=1)
    out, dropped = remove_doubles(doubled)
    assert np.array_equal(out, base)
    assert dropped == doubled.size - base.size


def test_remove_doubles_keeps_deliberately_flat_art():
    idx = np.array([[1, 1, 1], [1, 1, 1]], dtype=np.int32)
    out, _ = remove_doubles(idx)
    assert out.shape == (1, 1)  # genuinely one colour — collapsing is correct
    idx2 = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.int32)
    out2, dropped2 = remove_doubles(idx2)
    assert np.array_equal(out2, idx2) and dropped2 == 0


def test_snap_grid_forces_each_cell_to_its_modal_colour():
    idx = np.full((4, 4), 1, dtype=np.int32)
    idx[0, 0] = 9  # one stray pixel inside the top-left 2x2 cell
    out, changed = snap_grid(idx, cell_w=2, cell_h=2)
    assert (out == 1).all()
    assert changed == 1


def test_snap_grid_rejects_a_zero_cell_with_a_usable_message():
    with pytest.raises(ToolError) as e:
        snap_grid(np.zeros((4, 4), dtype=np.int32), cell_w=0, cell_h=2)
    assert e.value.code == "snap_grid_bad_cell"
    assert "detect_grid" in str(e.value)


def test_new_ops_are_reachable_from_the_pipeline_and_report_counts():
    assert "close_silhouette" in OPERATIONS and "thin_lines" in OPERATIONS
    idx = np.full((8, 8), 2, dtype=np.int32)
    idx[4, 4] = 0
    out, report = run_pipeline(idx, ["#000000", "#ff0000", "#00ff00"], ["close_silhouette"], 0.5)
    assert report["close_silhouette"] == 1
    assert out[4, 4] == 2


def test_pipeline_preserves_shape_for_every_listed_operation():
    """The cleanup tool diffs input against output pixel for pixel, so any
    operation in OPERATIONS must not resize — that is why remove_doubles and
    snap_grid are excluded from it."""
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
