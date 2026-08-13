"""Turn an arbitrary image into true, grid-correct, palette-locked pixel art.
Chains crop -> grid snap -> two-stage downscale -> OKLab quantize -> optional
dither -> alpha threshold. See aseprite-mcp-upgrade-plan.md B4. Order matters:
this specific sequence, not any of its steps alone, is the product.
"""

from typing import Literal, TypedDict

import numpy as np
from PIL import Image

from .cleanup import enforce_palette
from .color import BAYER, quantize_rgb
from .grid import GridInfo, detect_grid, snap_to_grid

Dither = Literal["none", "bayer2x2", "bayer4x4"]


class ConformReport(TypedDict):
    grid: GridInfo
    crop_box: tuple[int, int, int, int]
    target_size: tuple[int, int]
    dither: str


def split_alpha(rgba: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """rgba (H,W,4) float [0,1] -> (rgb (H,W,3), alpha (H,W)). Alpha is never
    quantized alongside color — it gets its own binary threshold at the end."""
    return rgba[..., :3], rgba[..., 3]


def crop_to_content(rgb: np.ndarray, alpha: np.ndarray) -> tuple[np.ndarray, np.ndarray, tuple[int, int, int, int]]:
    """Bounding box of non-transparent content, so the subject fills the target
    canvas instead of floating in a sea of margin. `crop_box` is (x0, y0, x1, y1)
    in source pixel coordinates, for callers that need to map back."""
    ys, xs = np.where(alpha > 0)
    if ys.size == 0:
        h, w = alpha.shape
        return rgb, alpha, (0, 0, w, h)
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    return rgb[y0:y1, x0:x1], alpha[y0:y1, x0:x1], (x0, y0, x1, y1)


def resize_lanczos(rgb: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """size is (w, h). LANCZOS for the structure-preserving intermediate step —
    never the final step (CLAUDE.md #13, #10): it's the right tool for a large
    reduction but leaves soft edges wrong for a final pixel grid."""
    im = Image.fromarray((np.clip(rgb, 0, 1) * 255).round().astype(np.uint8), "RGB")
    im = im.resize(size, Image.Resampling.LANCZOS)
    return np.asarray(im, dtype=float) / 255.0


def resize_nearest(mask: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """size is (w, h). Nearest-neighbor resample of a single-channel array."""
    im = Image.fromarray((np.clip(mask, 0, 1) * 255).round().astype(np.uint8), "L")
    im = im.resize(size, Image.Resampling.NEAREST)
    return np.asarray(im, dtype=float) / 255.0


def _modal_color(block: np.ndarray) -> np.ndarray:
    """2-means on a block of RGB pixels, returns the larger cluster's centroid.
    Preserves hard edges that a straight mean would blur: a block straddling
    two flat colors comes out as one of them, not a blend."""
    n = block.shape[0]
    if n <= 2:
        return np.asarray(block.mean(axis=0))
    c1, c2 = block.min(axis=0), block.max(axis=0)
    if np.allclose(c1, c2):
        return np.asarray(block.mean(axis=0))
    mask1 = np.ones(n, dtype=bool)
    for _ in range(4):
        d1 = ((block - c1) ** 2).sum(axis=1)
        d2 = ((block - c2) ** 2).sum(axis=1)
        new_mask1 = d1 <= d2
        if new_mask1.all() or not new_mask1.any():
            mask1 = new_mask1
            break
        mask1 = new_mask1
        c1 = block[mask1].mean(axis=0)
        c2 = block[~mask1].mean(axis=0)
    n1 = int(mask1.sum())
    if n1 == 0:
        return np.asarray(c2)
    if n1 == n:
        return np.asarray(c1)
    return np.asarray(c1 if n1 >= n - n1 else c2)


def downscale_modal(rgb: np.ndarray, target: tuple[int, int]) -> np.ndarray:
    """Final downscale by majority vote per cell rather than averaging (target
    is (w, h)). Averaging blurs hard edges into mud; the mode preserves them —
    this is the piece that distinguishes conform from a naive resize."""
    h, w = rgb.shape[:2]
    tw, th = target
    ys = np.linspace(0, h, th + 1).astype(int)
    xs = np.linspace(0, w, tw + 1).astype(int)
    out = np.zeros((th, tw, 3))
    for j in range(th):
        for i in range(tw):
            block = rgb[ys[j]:ys[j + 1], xs[i]:xs[i + 1]].reshape(-1, 3)
            out[j, i] = _modal_color(block) if block.size else rgb[min(ys[j], h - 1), min(xs[i], w - 1)]
    return out


def apply_bayer(rgb: np.ndarray, palette_hex: list[str], pattern: Dither, lightness_weight: float) -> np.ndarray:
    """Ordered dithering before quantization. Floyd-Steinberg is deliberately
    not offered — it's noise at pixel-art scale (CLAUDE.md #14)."""
    matrix = BAYER[pattern]
    h, w = rgb.shape[:2]
    threshold = np.tile(matrix, (h // matrix.shape[0] + 1, w // matrix.shape[1] + 1))[:h, :w]
    nudged = np.clip(rgb + (threshold[:, :, None] - 0.5) / 8.0, 0.0, 1.0)
    return quantize_rgb(nudged, palette_hex, weights=(lightness_weight, 1.0, 1.0))


def conform(
    rgba: np.ndarray,
    target_size: tuple[int, int],
    palette_hex: list[str],
    grid: GridInfo | None = None,
    dither: Dither = "none",
    lightness_weight: float = 1.3,
) -> tuple[np.ndarray, np.ndarray, ConformReport]:
    """rgba (H,W,4) float [0,1] -> (palette indices (th,tw), alpha mask (th,tw)
    bool, report). `target_size` is (w, h). `grid` skips auto-detection when
    the caller already knows the source's pixel-block size.
    """
    tw, th = target_size
    rgb, alpha = split_alpha(rgba)
    rgb, alpha, crop_box = crop_to_content(rgb, alpha)

    g = grid or detect_grid(rgb)
    if g["is_pixel_art"]:
        rgb = snap_to_grid(rgb, g)

    h, w = rgb.shape[:2]
    mid_w, mid_h = max(tw * 2, 1), max(th * 2, 1)
    if w > mid_w and h > mid_h:
        rgb = resize_lanczos(rgb, (mid_w, mid_h))
    rgb = downscale_modal(rgb, (tw, th))

    if dither == "none":
        idx = enforce_palette(rgb, palette_hex, lightness_weight)
    else:
        idx = apply_bayer(rgb, palette_hex, dither, lightness_weight)

    alpha_mask = resize_nearest(alpha, (tw, th)) > 0.5

    report: ConformReport = {
        "grid": g,
        "crop_box": crop_box,
        "target_size": (tw, th),
        "dither": dither,
    }
    return idx, alpha_mask, report
