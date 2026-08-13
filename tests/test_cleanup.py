import numpy as np

from aseprite_mcp.cleanup import (
    OPERATIONS,
    enforce_palette,
    fix_jaggies,
    merge_near_colors,
    remove_antialiasing,
    remove_orphans,
    run_pipeline,
)


def _staircase(runs: list[int], start_edge: int = 5, size: int = 32) -> np.ndarray:
    """32x32 of color 0 with a color-1 region on the left whose right edge is a
    staircase with the given run lengths (rows per step, stepping +1 column)."""
    idx = np.zeros((size, size), dtype=np.uint8)
    y, edge = 0, start_edge
    for n in runs:
        idx[y:y + n, : edge + 1] = 1
        y, edge = y + n, edge + 1
    idx[y:, : edge] = 1  # keep the region contiguous down to the bottom edge
    return idx


def _right_edges(idx: np.ndarray, rows: int) -> list[int]:
    return [int(np.flatnonzero(idx[y] == 1)[-1]) for y in range(rows)]


def test_remove_orphans_clears_injected_specks():
    rng = np.random.default_rng(42)
    base = np.zeros((32, 32), dtype=np.uint8)
    base[:, 16:] = 1  # two solid regions, colors 0 and 1

    idx = base.copy()
    ys = rng.integers(0, 32, size=20)
    xs = rng.integers(0, 32, size=20)
    for y, x in zip(ys, xs):
        idx[y, x] = 2  # a color that appears nowhere else -> guaranteed orphan

    cleaned, removed = remove_orphans(idx, min_size=2, protect=frozenset({0, 1}))

    assert not (cleaned == 2).any()
    assert removed == 20
    # unaffected pixels away from specks keep their original two-region layout
    assert np.array_equal(cleaned[:, :16] * (base[:, :16] == cleaned[:, :16]), cleaned[:, :16])


def test_remove_orphans_leaves_real_regions_alone():
    idx = np.zeros((10, 10), dtype=np.uint8)
    idx[2:8, 2:8] = 1  # a real 6x6 block, well above min_size

    cleaned, removed = remove_orphans(idx, min_size=2, protect=frozenset({0}))

    assert removed == 0
    assert np.array_equal(cleaned, idx)


def test_remove_antialiasing_snaps_blended_edge_to_two_colors():
    # a hard diagonal edge between pure red and pure blue, blended across
    # a 3px-wide AA band with 3 intermediate levels
    h = w = 12
    rgb = np.zeros((h, w, 3), dtype=float)
    red = np.array([1.0, 0.0, 0.0])
    blue = np.array([0.0, 0.0, 1.0])
    for y in range(h):
        for x in range(w):
            d = x - y  # signed distance from the diagonal
            if d <= -2:
                rgb[y, x] = red
            elif d >= 2:
                rgb[y, x] = blue
            else:
                t = (d + 2) / 4.0  # 0, 1/4, 2/4, 3/4, 1 across the band
                rgb[y, x] = (1 - t) * red + t * blue

    out = remove_antialiasing(rgb, threshold=0.15)
    # exclude the outer 1px ring: the diagonal passes exactly through the
    # image corners there, so the 3x3 window (edge-padded) can't reach a
    # pure color within one step — a boundary limitation of the windowed
    # algorithm itself, not of interior behavior.
    colors = {tuple(np.round(out[y, x], 3)) for y in range(1, h - 1) for x in range(1, w - 1)}
    assert len(colors) == 2


def test_fix_jaggies_regularizes_irregular_diagonal():
    idx = _staircase([2, 2, 3, 2, 1, 2])
    out, changed = fix_jaggies(idx, max_correction=1)

    # the 6 steps span 12 rows either way; after the fix every run is 2 long
    assert _right_edges(out, 12) == [5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10]
    assert changed > 0


def test_fix_jaggies_leaves_intentional_run_alone():
    idx = _staircase([2, 2, 5, 2])
    out, changed = fix_jaggies(idx, max_correction=1)

    assert changed == 0
    assert np.array_equal(out, idx)


def test_fix_jaggies_is_idempotent():
    once, _ = fix_jaggies(_staircase([2, 2, 3, 2, 1, 2]), max_correction=1)
    twice, changed = fix_jaggies(once, max_correction=1)

    assert changed == 0
    assert np.array_equal(twice, once)


def test_enforce_palette_maps_to_exact_indices():
    palette = ["#ff0000", "#00ff00", "#0000ff"]
    rgb = np.array([
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        [[0.0, 0.0, 1.0], [0.9, 0.05, 0.05]],  # near-red -> index 0
    ])

    assert np.array_equal(enforce_palette(rgb, palette), np.array([[0, 1], [2, 0]]))


# index 0 is transparency; 1/2/3 are a grayscale ramp, so index 2 sitting
# between 1 and 3 reads exactly like an anti-aliasing blend
_RAMP = ["#000000", "#555555", "#aaaaaa", "#ffffff"]


def _fringed_sprite() -> np.ndarray:
    """A dark block on a light field, ringed by a mid-tone AA fringe, plus three
    stray single pixels of the block color out in the open field."""
    idx = np.full((16, 16), 3, dtype=np.int32)
    idx[4:12, 4:12] = 1
    idx[3, 3:13] = idx[12, 3:13] = 2
    idx[3:13, 3] = idx[3:13, 12] = 2
    idx[1, 1] = idx[14, 9] = idx[6, 14] = 1
    return idx


def test_run_pipeline_reports_per_operation_and_is_idempotent():
    idx = _fringed_sprite()

    once, report = run_pipeline(idx, _RAMP, list(OPERATIONS), aggressiveness=0.5)
    assert set(report) == set(OPERATIONS)
    assert report["remove_aa"] > 0 and report["remove_orphans"] > 0
    assert not (once == 2).any()  # the fringe snapped to one side or the other
    assert (once[1, 1], once[14, 9], once[6, 14]) == (3, 3, 3)  # specks absorbed

    twice, report2 = run_pipeline(once, _RAMP, list(OPERATIONS), aggressiveness=0.5)
    assert sum(report2.values()) == 0
    assert np.array_equal(twice, once)


def test_run_pipeline_skips_operations_not_requested():
    idx = _fringed_sprite()
    out, report = run_pipeline(idx, _RAMP, ["remove_orphans"], aggressiveness=0.5)

    assert list(report) == ["remove_orphans"]
    assert (out == 2).sum() == (idx == 2).sum()  # the fringe was left alone


def test_run_pipeline_never_quantizes_transparency():
    """Index 0 is alpha, not a color. Round-tripping through RGB would map it to
    palette entry 0's color and let AA snapping eat holes in the alpha mask."""
    idx = np.full((12, 12), 0, dtype=np.int32)
    idx[3:9, 3:9] = 1
    idx[2, 2:10] = idx[9, 2:10] = 2  # fringe sitting on the transparent border
    idx[2:10, 2] = idx[2:10, 9] = 2

    out, _ = run_pipeline(idx, _RAMP, ["remove_aa"], aggressiveness=1.0)

    assert np.array_equal(out == 0, idx == 0)  # alpha mask identical either way


def test_merge_near_colors_collapses_a_chain_of_near_duplicates():
    # indices 1,2,3 are near-identical blues a tiny OKLab step apart -- a
    # painterly source's MEDIANCUT extraction leaves chains like this. Index 4
    # is a genuinely distinct color and must survive untouched.
    palette = ["#000000", "#1a2a3a", "#1c2c3c", "#1e2e3e", "#ff8800"]
    idx = np.array([[0, 1, 2], [3, 4, 1]], dtype=np.int32)

    out, changed = merge_near_colors(idx, palette, threshold=0.05)

    merged_to = {int(out[0, 1]), int(out[0, 2]), int(out[1, 0]), int(out[1, 2])}
    assert len(merged_to) == 1  # 1, 2, 3 all collapsed to the same index
    assert out[1, 1] == 4  # the distinct color untouched
    assert changed > 0


def test_merge_near_colors_never_touches_transparency():
    # index 0 sits numerically close to index 1 in OKLab but must never merge --
    # merging into 0 would silently turn opaque pixels transparent.
    palette = ["#000000", "#010101"]
    idx = np.array([[0, 1]], dtype=np.int32)

    out, changed = merge_near_colors(idx, palette, threshold=1.0)

    assert changed == 0
    assert np.array_equal(out, idx)


def test_merge_near_colors_is_a_noop_on_a_well_separated_palette():
    out, changed = merge_near_colors(_fringed_sprite(), _RAMP, threshold=0.03)
    assert changed == 0
