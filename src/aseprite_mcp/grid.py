"""Detect the effective pixel-block size of a pseudo-pixel-art image — an 8x
nearest-neighbour upscale of a 32x32 sprite is a 256x256 image whose real
resolution is 32x32, and conform needs to know that before it downscales.

See aseprite-mcp-upgrade-plan.md B3. The plan sketches this as autocorrelation
of the per-axis edge energy, scored by how far the peak stands out from the
local mean. That finds the period but its confidence number is arbitrary — the
plan divides by 6.0 with a "normalize to ~0-1" note. Scoring the grid fit
directly gives the same period and a confidence that means something: the share
of edge energy that lands on grid lines, measured against what an unaligned
image would score by chance. 1.0 is a perfect grid, 0.0 is a photograph.
"""

from typing import TypedDict

import numpy as np

from .color import rgb_to_oklab

# Below this, the "grid" is chance alignment — conform should downscale by plain
# ratio rather than snapping to a grid that isn't there.
_PIXEL_ART_THRESHOLD = 0.5

# Cell sizes scoring within this of the best are treated as tied, and the
# largest wins. Every divisor of the true cell size captures all the edge
# energy too (a 4-grid contains every line of an 8-grid), so without this the
# answer is always min_cell.
_TIE_TOLERANCE = 0.02


class GridInfo(TypedDict):
    cell_w: int | None
    cell_h: int | None
    offset_x: int
    offset_y: int
    confidence: float
    is_pixel_art: bool


def _edge_energy(lab: np.ndarray, axis: int) -> np.ndarray:
    """Per-boundary color change summed across the other axis. Element i is the
    energy of the boundary between index i and i+1."""
    return np.abs(np.diff(lab, axis=axis)).sum(axis=(1 - axis, 2))


def _axis_grid(energy: np.ndarray, min_cell: int, max_cell: int) -> tuple[int | None, int, float]:
    """Best (cell, offset, confidence) for one axis."""
    total = float(energy.sum())
    n = len(energy)
    if total <= 0 or n < 2 * min_cell:
        return None, 0, 0.0

    scored: list[tuple[int, int, float]] = []
    for cell in range(min_cell, min(max_cell, n // 2) + 1):
        # score every phase; the boundary between cell k-1 and k sits at
        # energy index cell*k - 1, so phase p means a grid origin at p+1
        by_phase = [float(energy[p::cell].sum()) for p in range(cell)]
        phase = int(np.argmax(by_phase))
        on_grid = by_phase[phase] / total
        chance = len(energy[phase::cell]) / n
        scored.append((cell, phase, (on_grid - chance) / (1.0 - chance)))

    if not scored:
        return None, 0, 0.0
    best = max(c for _, _, c in scored)
    cell, phase, conf = max(
        (s for s in scored if s[2] >= best - _TIE_TOLERANCE), key=lambda s: s[0]
    )
    return cell, (phase + 1) % cell, conf


def detect_grid(rgb: np.ndarray, min_cell: int = 2, max_cell: int = 32) -> GridInfo:
    """rgb (H,W,3) float [0,1]. Returns the detected cell size, the grid origin
    offset, and a confidence in [0,1].

    High confidence means the image really is on a pixel grid (or a clean
    upscale of one) and conform should snap each cell to its modal color. Low
    confidence means a photo or a smooth render — downscale by ratio instead,
    because snapping to a grid that isn't there quantizes noise.
    """
    lab = rgb_to_oklab(rgb)
    cell_w, off_x, conf_w = _axis_grid(_edge_energy(lab, axis=1), min_cell, max_cell)
    cell_h, off_y, conf_h = _axis_grid(_edge_energy(lab, axis=0), min_cell, max_cell)

    # Both axes must agree: a striped photo can look gridded on one axis alone.
    confidence = min(conf_w, conf_h)
    return GridInfo(
        cell_w=cell_w,
        cell_h=cell_h,
        offset_x=off_x,
        offset_y=off_y,
        confidence=float(confidence),
        is_pixel_art=bool(
            cell_w is not None and cell_h is not None and confidence >= _PIXEL_ART_THRESHOLD
        ),
    )


def snap_to_grid(rgb: np.ndarray, info: GridInfo) -> np.ndarray:
    """Force every detected cell to a single color — its mean, which for a clean
    upscale is exactly the original pixel and for a slightly-resampled one is the
    dominant color plus a little edge contamination. Undoes the soft cell borders
    left by a bilinear or JPEG round-trip."""
    cw, ch = info["cell_w"], info["cell_h"]
    if not cw or not ch:
        return rgb
    out = rgb.copy()
    h, w = rgb.shape[:2]
    for y0 in range(-((ch - info["offset_y"]) % ch), h, ch):
        for x0 in range(-((cw - info["offset_x"]) % cw), w, cw):
            ys, xs = slice(max(y0, 0), y0 + ch), slice(max(x0, 0), x0 + cw)
            cell = out[ys, xs]
            if cell.size:
                out[ys, xs] = cell.reshape(-1, 3).mean(axis=0)
    return out
