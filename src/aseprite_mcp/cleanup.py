"""Deterministic pixel-art repair: orphan speck removal, anti-aliasing removal,
jaggie regularization, palette enforcement. Operates on plain numpy arrays — no
Aseprite bridge involved, unit-testable. See aseprite-mcp-upgrade-plan.md B2.
"""

import numpy as np
from scipy import ndimage
from skimage.measure import label

from .color import oklab_to_rgb, palette_lab, quantize_rgb, rgb_to_oklab
from .errors import ToolError
from .validation import hex_to_rgba


def remove_orphans(
    idx: np.ndarray, min_size: int = 2, protect: frozenset[int] = frozenset({0})
) -> tuple[np.ndarray, int]:
    """Remove connected components smaller than min_size, replacing with the
    majority color in a dilated neighborhood. Returns (result, n_removed)."""
    out = idx.copy()
    removed = 0
    for color in np.unique(idx):
        if int(color) in protect:
            continue
        mask = idx == color
        comp_labels, n = label(mask, connectivity=2, return_num=True)
        for comp_id in range(1, n + 1):
            comp = comp_labels == comp_id
            size = int(comp.sum())
            if size >= min_size:
                continue
            ring = ndimage.binary_dilation(comp, np.ones((3, 3), dtype=bool)) & ~comp
            neighbors = out[ring]
            if neighbors.size:
                vals, counts = np.unique(neighbors, return_counts=True)
                out[comp] = vals[counts.argmax()]
                removed += size
    return out, removed


def remove_antialiasing(rgb: np.ndarray, threshold: float = 0.12) -> np.ndarray:
    """rgb (H,W,3) float [0,1]. For each pixel, find the two most distant
    colors in its 3x3 neighborhood; if the pixel lies near the segment
    between them in OKLab, it's a blend — snap it to the closer endpoint."""
    lab = rgb_to_oklab(rgb)
    h, w = lab.shape[:2]
    out = rgb.copy()
    padded = np.pad(lab, ((1, 1), (1, 1), (0, 0)), mode="edge")

    for y in range(h):
        for x in range(w):
            nb = padded[y:y + 3, x:x + 3].reshape(-1, 3)
            c = lab[y, x]
            d = ((nb[:, None, :] - nb[None, :, :]) ** 2).sum(-1)
            i, j = np.unravel_index(d.argmax(), d.shape)
            a, b = nb[i], nb[j]
            ab = b - a
            seg_len_sq = (ab ** 2).sum()
            if seg_len_sq < 1e-8:
                continue
            t = float(np.clip(((c - a) @ ab) / seg_len_sq, 0, 1))
            proj = a + t * ab
            dist_to_segment = float(np.sqrt(((c - proj) ** 2).sum()))
            # near the segment AND meaningfully interior => it's a blend
            if dist_to_segment < threshold and 0.2 < t < 0.8:
                snap = a if t < 0.5 else b
                out[y, x] = oklab_to_rgb(snap)
    return out


# --- Jaggie regularization ---------------------------------------------------
# Pixel-art diagonals follow a grammar: consistent run lengths (2-2-2, 3-3-3).
# Downscaling and diffusion break it (2-2-3-1-2). Detect the modal run length
# along each monotone staircase and pull small deviations back to it. Large
# deviations are intentional shape features and are never touched.


def _regularize_lengths(lengths: list[int], max_correction: int) -> list[int]:
    """Snap interior run lengths to the modal length, preserving the total.

    The first and last runs of a staircase are clipped by the region border, so
    their length carries no information — they are left alone (and the last one
    absorbs any residual drift so the profile keeps its original height).
    """
    vals, counts = np.unique(lengths, return_counts=True)
    mode = int(vals[counts.argmax()])
    out = list(lengths)
    for i in range(1, len(lengths) - 1):
        if 0 < abs(lengths[i] - mode) <= max_correction:
            out[i] = mode
    drift = sum(out) - sum(lengths)
    out[-1] -= drift
    return lengths if out[-1] < 1 else out


def _regularize_profile(prof: np.ndarray, max_correction: int) -> np.ndarray:
    """prof: edge position per row. Returns the regularized profile, same length."""
    runs: list[list[int]] = []
    for v in prof:
        if runs and runs[-1][0] == int(v):
            runs[-1][1] += 1
        else:
            runs.append([int(v), 1])
    if len(runs) < 3:
        return prof.copy()

    fixed: list[list[int]] = []
    i = 0
    while i < len(runs):
        # extend a maximal stretch of unit steps in one consistent direction;
        # anything else (a jump of 2+, a reversal) is a real corner, not a jaggie
        j, step = i + 1, None
        while j < len(runs):
            d = runs[j][0] - runs[j - 1][0]
            if abs(d) != 1 or (step is not None and d != step):
                break
            step = d
            j += 1
        stretch = runs[i:j]
        if len(stretch) >= 3:
            new_lengths = _regularize_lengths([r[1] for r in stretch], max_correction)
            stretch = [[r[0], n] for r, n in zip(stretch, new_lengths)]
        fixed.extend(stretch)
        i = j

    new = np.concatenate([np.full(n, v) for v, n in fixed])
    return new if new.size == prof.size else prof.copy()


def _edge_profile(mask: np.ndarray, right: bool) -> np.ndarray:
    """Per-row index of the first (or last) set pixel; -1 for empty rows."""
    prof = np.full(mask.shape[0], -1, dtype=int)
    for y in range(mask.shape[0]):
        xs = np.flatnonzero(mask[y])
        if xs.size:
            prof[y] = int(xs[-1] if right else xs[0])
    return prof


def _spans(valid: np.ndarray) -> list[tuple[int, int]]:
    """Half-open [start, stop) ranges of contiguous True values."""
    out, start = [], None
    for i, v in enumerate(valid):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(valid)))
    return out


def _shift_edge(out: np.ndarray, y: int, old: int, new: int, color: int, right: bool) -> int:
    """Move one row's edge from `old` to `new`. Growing paints `color`; shrinking
    paints whatever sits just outside the old edge. Returns pixels changed."""
    if right:
        lo, hi = (old + 1, new + 1) if new > old else (new + 1, old + 1)
        fill = color if new > old else int(out[y, old + 1]) if old + 1 < out.shape[1] else 0
    else:
        lo, hi = (new, old) if new < old else (old, new)
        fill = color if new < old else int(out[y, old - 1]) if old > 0 else 0
    lo, hi = max(lo, 0), min(hi, out.shape[1])
    if hi <= lo:
        return 0
    out[y, lo:hi] = fill
    return hi - lo


def fix_jaggies(idx: np.ndarray, max_correction: int = 1) -> tuple[np.ndarray, int]:
    """Regularize staircase run lengths along each color region's left and right
    edges. Runs deviating from their staircase's modal length by more than
    `max_correction` are left alone — those are shape, not artifact.

    Returns (result, n_pixels_changed).

    ponytail: row profiles only, so this fixes steep diagonals (run length along
    y). Shallow diagonals step by 2+ columns per row, which reads as a corner and
    is skipped rather than mangled. Add a transposed pass if they need fixing too.
    """
    out = idx.copy()
    changed = 0
    for color in np.unique(idx):
        for right in (False, True):
            prof = _edge_profile(out == color, right)
            # each contiguous block of non-empty rows is its own staircase
            for y0, y1 in _spans(prof >= 0):
                seg = prof[y0:y1]
                new = _regularize_profile(seg, max_correction)
                for k in range(y1 - y0):
                    if new[k] != seg[k]:
                        changed += _shift_edge(
                            out, y0 + k, int(seg[k]), int(new[k]), int(color), right
                        )
    return out, changed


def merge_near_colors(
    idx: np.ndarray,
    palette_hex: list[str],
    threshold: float = 0.03,
    protect: frozenset[int] = frozenset({0}),
) -> tuple[np.ndarray, int]:
    """Remap pixels using palette entries within OKLab distance `threshold` of
    each other onto a single representative index (transitively -- a chain of
    five near-identical blues collapses to one, not five pairs).

    Fixes a specific failure mode of downscale_modal on continuously-shaded
    (painterly) source art: a 32-color palette extracted from a gradient often
    contains many near-duplicate shades a step apart, and downscale_modal's
    per-block 2-means clustering picks a different one of them in each
    neighboring block almost arbitrarily, leaving flat regions looking like
    salt-and-pepper noise even though every pixel is on-palette and no single
    pixel is individually wrong. Confirmed empirically (B6 gate re-run):
    remove_aa/remove_orphans/fix_jaggies don't touch this pattern at any
    aggressiveness -- the "noise" pixels are 20-40px fragments, past the size
    orphan removal can touch without eating real detail, and they aren't
    blends, so remove_aa correctly leaves them alone.
    """
    pal_lab = palette_lab(palette_hex)
    n = len(palette_hex)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            a = parent[a]
        return a

    for i in range(n):
        if i in protect:
            continue
        for j in range(i + 1, n):
            if j in protect:
                continue
            if float(np.sqrt(((pal_lab[i] - pal_lab[j]) ** 2).sum())) < threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)

    remap = np.array([find(i) for i in range(n)])
    out = remap[idx]
    return out, int((out != idx).sum())


def enforce_palette(
    rgb: np.ndarray,
    palette_hex: list[str],
    lightness_weight: float = 1.3,
) -> np.ndarray:
    """rgb (H,W,3) float [0,1] -> (H,W) indices into palette_hex, matched in
    OKLab with lightness weighted up: value structure carries shape in pixel
    art, hue errors are more forgivable."""
    return quantize_rgb(rgb, palette_hex, weights=(lightness_weight, 1.0, 1.0))


def close_silhouette(
    idx: np.ndarray, radius: int = 1, transparent: int = 0
) -> tuple[np.ndarray, int]:
    """Binary-close the opaque mask to fill 1px pinholes, then open it to shave
    1px protrusions. Filled holes take the majority color of their own ring, so
    closing never invents a color that wasn't already local to the gap.

    Downscaling a shape with a thin neck routinely punches a hole through it or
    leaves a single-pixel whisker hanging off the edge; both read as damage
    rather than as an intentional shape.
    """
    st = np.ones((2 * radius + 1, 2 * radius + 1), dtype=bool)
    solid = idx != transparent
    closed = ndimage.binary_closing(solid, st)
    # border_value=1 so the array edge counts as filled. Without it the erosion
    # half of the opening treats everything outside the canvas as background and
    # eats a 1px border off any shape that runs to the edge — which for a sprite
    # cropped to its own content is most of them.
    opened = ndimage.binary_opening(closed, st, border_value=1)

    out = idx.copy()
    out[solid & ~opened] = transparent  # protrusions shaved off

    filled = opened & ~solid
    if filled.any():
        comps, n = label(filled, connectivity=2, return_num=True)
        for comp_id in range(1, n + 1):
            comp = comps == comp_id
            ring = ndimage.binary_dilation(comp, np.ones((3, 3), dtype=bool)) & solid
            neighbors = idx[ring]
            if neighbors.size:
                vals, counts = np.unique(neighbors, return_counts=True)
                out[comp] = vals[counts.argmax()]
    return out, int((out != idx).sum())


def thin_lines(idx: np.ndarray, transparent: int = 0) -> tuple[np.ndarray, int]:
    """Collapse 2px-wide runs of a color back to 1px where the run is a line
    rather than a filled region.

    A run qualifies only if it is 2px in one axis AND the color forms a thin
    structure there — measured by the component's own thickness, so a 2px-wide
    detail inside a large filled shape is left alone. Removed pixels take the
    color of whatever is on the outer side, so the line thins rather than
    growing a hole.
    """
    out = idx.copy()
    for color in np.unique(idx):
        if color == transparent:
            continue
        mask = idx == color
        # distance transform: max value is the component's half-thickness, so a
        # genuine 1-2px line peaks at 1, a filled blob peaks much higher
        dist = ndimage.distance_transform_cdt(mask, metric="chessboard")
        comps, n = label(mask, connectivity=2, return_num=True)
        for comp_id in range(1, n + 1):
            comp = comps == comp_id
            if comp.sum() < 4 or dist[comp].max() > 1:
                continue  # not a thin line — leave filled regions alone
            ys, xs = np.nonzero(comp)
            # Thin perpendicular to the line's own direction: a horizontal line
            # is 2px tall and must lose a row, a vertical one is 2px wide and
            # must lose a column. Scanning only one axis (the first cut of this
            # did) silently no-ops on half of all lines.
            horizontal = (xs.max() - xs.min()) >= (ys.max() - ys.min())
            axis = comp.T if horizontal else comp
            for line_no in range(axis.shape[0]):
                on = axis[line_no]
                if not on.any():
                    continue
                for start, end in _spans(on):
                    if end - start != 2:
                        continue
                    # drop the far pixel — bottom of a horizontal line, right of
                    # a vertical one, i.e. the side an upper-left light hides
                    drop = end - 1
                    if horizontal:
                        out[drop, line_no] = transparent
                    else:
                        out[line_no, drop] = transparent
    return out, int((out != idx).sum())


def remove_doubles(idx: np.ndarray) -> tuple[np.ndarray, int]:
    """Collapse duplicated adjacent rows/columns left by grid misdetection.

    When `detect_grid` picks a cell one pixel off, the resample emits the same
    row twice; the sprite then reads as the right art at the wrong aspect. Only
    fully-identical neighbours collapse, so deliberately flat art is untouched.
    Returns a possibly *smaller* array — callers must handle the shape change.
    """
    rows = [0] + [y for y in range(1, idx.shape[0]) if not np.array_equal(idx[y], idx[y - 1])]
    tmp = idx[rows]
    cols = [0] + [x for x in range(1, tmp.shape[1]) if not np.array_equal(tmp[:, x], tmp[:, x - 1])]
    out = tmp[:, cols]
    return out, int(idx.size - out.size)


def snap_grid(idx: np.ndarray, cell_w: int, cell_h: int, offset_x: int = 0, offset_y: int = 0) -> tuple[np.ndarray, int]:
    """Force every detected grid cell to a single color — its own modal color.

    Pairs with `grid.detect_grid`: once a cell size is known with confidence,
    any within-cell variation is resample noise by definition, because the
    source art had one color there.
    """
    if cell_w < 1 or cell_h < 1:
        raise ToolError(
            code="snap_grid_bad_cell",
            message=f"cell_w={cell_w}, cell_h={cell_h} — both must be >= 1.",
            hint="Take these from detect_grid, and only when is_pixel_art is true.",
        )
    out = idx.copy()
    H, W = idx.shape
    for y0 in range(-(offset_y % cell_h), H, cell_h):
        for x0 in range(-(offset_x % cell_w), W, cell_w):
            block = idx[max(0, y0) : y0 + cell_h, max(0, x0) : x0 + cell_w]
            if block.size == 0:
                continue
            vals, counts = np.unique(block, return_counts=True)
            out[max(0, y0) : y0 + cell_h, max(0, x0) : x0 + cell_w] = vals[counts.argmax()]
    return out, int((out != idx).sum())


# --- Pipeline ----------------------------------------------------------------

# Order here is the order they run in (see run_pipeline), not the order given.
OPERATIONS = (
    "merge_near_colors",
    "remove_aa",
    "remove_orphans",
    "close_silhouette",
    "thin_lines",
    "fix_jaggies",
)


def run_pipeline(
    idx: np.ndarray,
    palette_hex: list[str],
    operations: list[str],
    aggressiveness: float = 0.5,
) -> tuple[np.ndarray, dict[str, int]]:
    """Apply cleanup operations to an indexed image, in a fixed order regardless
    of the order requested: color merging collapses near-duplicate shades before
    anything else runs (AA/orphan detection is cleaner against fewer, more
    separated colors), AA removal creates specks, orphan removal clears them,
    jaggie regularization wants a settled edge to measure. Returns (result,
    pixels changed per operation).

    `aggressiveness` (0-1) maps onto each operation's threshold — one knob the
    model can turn instead of five it has to reason about.

    `enforce_palette` is deliberately not an operation here: the sprite is
    already indexed, so it would be a no-op. It belongs in the conform pipeline,
    where the input is arbitrary RGB.
    """
    a = float(np.clip(aggressiveness, 0.0, 1.0))
    pal_rgb = np.array([hex_to_rgba(h)[:3] for h in palette_hex], dtype=float) / 255.0
    out = idx.copy()
    report: dict[str, int] = {}

    for op in OPERATIONS:
        if op not in operations:
            continue
        before = out
        if op == "merge_near_colors":
            out, _ = merge_near_colors(out, palette_hex, threshold=0.01 + 0.04 * a)
        elif op == "remove_aa":
            rgb = remove_antialiasing(pal_rgb[out], threshold=0.06 + 0.14 * a)
            snapped = enforce_palette(rgb, palette_hex).astype(idx.dtype)
            # Index 0 is transparency, not a color. Round-tripping it through RGB
            # makes it whatever palette entry 0 happens to look like, so the mask
            # is restored verbatim — alpha is never quantized alongside color.
            transparent = before == 0
            snapped[transparent] = 0
            snapped[~transparent & (snapped == 0)] = before[~transparent & (snapped == 0)]
            out = snapped
        elif op == "remove_orphans":
            out, _ = remove_orphans(out, min_size=int(round(1 + 3 * a)))
        elif op == "close_silhouette":
            out, _ = close_silhouette(out, radius=1)
        elif op == "thin_lines":
            out, _ = thin_lines(out)
        elif op == "fix_jaggies":
            out, _ = fix_jaggies(out, max_correction=int(round(2 * a)))
        report[op] = int((out != before).sum())

    return out, report


# `snap_grid` and `remove_doubles` are deliberately absent from OPERATIONS.
# Both need information the pipeline doesn't have (a detected grid) or change
# the array's shape, and the cleanup tool diffs input against output pixel for
# pixel to build its edit list. They belong in the conform path, which resizes
# anyway — see conform.py. Calling them directly is fine.
