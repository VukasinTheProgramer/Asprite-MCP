"""Reference image preprocessing: load -> remove background -> crop -> downscale
-> quantize -> dither -> clean up orphan pixels. Pure Pillow/numpy — nothing here
touches Aseprite. See aseprite-mcp-build-flow.md §8.2 for the pipeline rationale.
"""

import numpy as np
from PIL import Image, ImageOps

from .color import BAYER, quantize_rgb, rgb_to_oklab

# OKLab conversion lives in color.py (shared with the Phase B conform/cleanup
# pipeline). RGB Euclidean distance picks visibly wrong hues for quantization
# (CLAUDE.md #12) — OKLab is perceptually uniform, so nearest-neighbor in
# this space tracks what a human would call "the closest color".

rgb_array_to_oklab = rgb_to_oklab  # back-compat alias for existing callers/tests


def quantize_to_palette(im: Image.Image, palette_hex: list[str]) -> np.ndarray:
    """Nearest palette color per pixel in OKLab space. Returns (H, W) int
    array of palette indices."""
    return quantize_rgb(np.array(im.convert("RGB"), dtype=float) / 255.0, palette_hex)


# --- Pipeline steps ----------------------------------------------------------


def load_and_orient(path: str) -> Image.Image:
    im: Image.Image = Image.open(path)
    im = ImageOps.exif_transpose(im) or im
    return im.convert("RGBA")


def remove_background(im: Image.Image, tolerance: int = 24) -> Image.Image:
    """Flood-fill transparency from the four corners, matching pixels within
    `tolerance` (per-channel) of each corner's color. Pixels that already
    have alpha are trusted as-is (no corner touches them if already 0)."""
    arr = np.array(im)
    h, w = arr.shape[:2]
    visited = np.zeros((h, w), dtype=bool)
    stack = [(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)]
    seeds = [(y, x) for y, x in stack if arr[y, x, 3] > 0]  # skip already-transparent corners
    for seed in seeds:
        seed_color = arr[seed][:3].astype(int)
        _flood(arr, visited, seed, seed_color, tolerance)
    return Image.fromarray(arr, "RGBA")


def _flood(arr: np.ndarray, visited: np.ndarray, seed: tuple[int, int], color: np.ndarray, tol: int) -> None:
    h, w = arr.shape[:2]
    stack = [seed]
    while stack:
        y, x = stack.pop()
        if y < 0 or y >= h or x < 0 or x >= w or visited[y, x]:
            continue
        visited[y, x] = True
        if np.abs(arr[y, x, :3].astype(int) - color).max() > tol:
            continue
        arr[y, x, 3] = 0
        stack.extend([(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)])


def crop_to_content(im: Image.Image) -> Image.Image:
    """Bounding box of non-transparent pixels — makes the subject fill the
    sprite instead of floating in a sea of margin."""
    bbox = im.split()[3].getbbox()  # alpha channel
    return im.crop(bbox) if bbox else im


def downscale(im: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """LANCZOS to ~2x target, then NEAREST for the last step. Straight-to-
    NEAREST loses detail; straight-to-LANCZOS produces mud (CLAUDE.md #13)."""
    mid_w, mid_h = max(target_w * 2, im.width // 2, 1), max(target_h * 2, im.height // 2, 1)
    mid_w, mid_h = min(mid_w, im.width), min(mid_h, im.height)
    if mid_w > target_w and mid_h > target_h:
        im = im.resize((mid_w, mid_h), Image.Resampling.LANCZOS)
    return im.resize((target_w, target_h), Image.Resampling.NEAREST)


def dither_indices(im: Image.Image, palette_hex: list[str], pattern: str) -> np.ndarray:
    """Ordered (Bayer) dithering. Floyd-Steinberg is default-off and not
    offered — it's noise at 32x32, wrong for pixel art (CLAUDE.md #14)."""
    if pattern == "none":
        return quantize_to_palette(im, palette_hex)
    matrix = BAYER[pattern]
    h, w = im.height, im.width
    threshold = np.tile(matrix, (h // matrix.shape[0] + 1, w // matrix.shape[1] + 1))[:h, :w]
    arr = np.array(im.convert("RGB"), dtype=float) / 255.0
    nudged = np.clip(arr + (threshold[:, :, None] - 0.5) / 8.0, 0.0, 1.0)
    return quantize_to_palette(Image.fromarray((nudged * 255).astype(np.uint8), "RGB"), palette_hex)


def remove_orphan_pixels(indices: np.ndarray) -> np.ndarray:
    """A pixel whose 4-connected neighbors are unanimously a different value
    is replaced with that neighbor value — downscaling generates these
    liberally (CLAUDE.md pitfall table)."""
    h, w = indices.shape
    out = indices.copy()
    for y in range(h):
        for x in range(w):
            neighbors = []
            if y > 0:
                neighbors.append(indices[y - 1, x])
            if y < h - 1:
                neighbors.append(indices[y + 1, x])
            if x > 0:
                neighbors.append(indices[y, x - 1])
            if x < w - 1:
                neighbors.append(indices[y, x + 1])
            if neighbors and all(n == neighbors[0] for n in neighbors) and neighbors[0] != indices[y, x]:
                out[y, x] = neighbors[0]
    return out
