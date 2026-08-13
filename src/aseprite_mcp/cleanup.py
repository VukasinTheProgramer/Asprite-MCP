"""Deterministic pixel-art repair: orphan speck removal, anti-aliasing removal,
jaggie regularization, palette enforcement. Operates on plain numpy arrays — no
Aseprite bridge involved, unit-testable. See aseprite-mcp-upgrade-plan.md B2.
"""

import numpy as np
from scipy import ndimage
from skimage.measure import label

from .color import oklab_to_rgb, quantize_rgb, rgb_to_oklab
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


def enforce_palette(
    rgb: np.ndarray,
    palette_hex: list[str],
    lightness_weight: float = 1.3,
) -> np.ndarray:
    """rgb (H,W,3) float [0,1] -> (H,W) indices into palette_hex, matched in
    OKLab with lightness weighted up: value structure carries shape in pixel
    art, hue errors are more forgivable."""
    return quantize_rgb(rgb, palette_hex, weights=(lightness_weight, 1.0, 1.0))


# --- Pipeline ----------------------------------------------------------------

OPERATIONS = ("remove_aa", "remove_orphans", "fix_jaggies")


def run_pipeline(
    idx: np.ndarray,
    palette_hex: list[str],
    operations: list[str],
    aggressiveness: float = 0.5,
) -> tuple[np.ndarray, dict[str, int]]:
    """Apply cleanup operations to an indexed image, in a fixed order regardless
    of the order requested: AA removal creates specks, orphan removal clears
    them, jaggie regularization wants a settled edge to measure. Returns
    (result, pixels changed per operation).

    `aggressiveness` (0-1) maps onto each operation's threshold — one knob the
    model can turn instead of four it has to reason about.

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
        if op == "remove_aa":
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
        elif op == "fix_jaggies":
            out, _ = fix_jaggies(out, max_correction=int(round(2 * a)))
        report[op] = int((out != before).sum())

    return out, report
