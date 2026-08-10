"""Reference image preprocessing: load -> remove background -> crop -> downscale
-> quantize -> dither -> clean up orphan pixels. Pure Pillow/numpy — nothing here
touches Aseprite. See aseprite-mcp-build-flow.md §8.2 for the pipeline rationale.
"""

import numpy as np
from PIL import Image, ImageOps

from .validation import hex_to_rgba

# --- OKLab -----------------------------------------------------------------
# Björn Ottosson's OKLab (https://bottosson.github.io/posts/oklab/). RGB
# Euclidean distance picks visibly wrong hues for quantization (CLAUDE.md
# #12) — OKLab is perceptually uniform, so nearest-neighbor in this space
# tracks what a human would call "the closest color".


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def rgb_array_to_oklab(arr: np.ndarray) -> np.ndarray:
    """arr: (..., 3) float in [0,1], sRGB. Returns (..., 3) OKLab."""
    lin = _srgb_to_linear(arr)
    r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]

    ll = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    mm = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    ss = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b

    ll_, mm_, ss_ = np.cbrt(ll), np.cbrt(mm), np.cbrt(ss)

    ok_l = 0.2104542553 * ll_ + 0.7936177850 * mm_ - 0.0040720468 * ss_
    ok_a = 1.9779984951 * ll_ - 2.4285922050 * mm_ + 0.4505937099 * ss_
    ok_b = 0.0259040371 * ll_ + 0.7827717662 * mm_ - 0.8086757660 * ss_
    return np.stack([ok_l, ok_a, ok_b], axis=-1)


def quantize_to_palette(im: Image.Image, palette_hex: list[str]) -> np.ndarray:
    """Nearest palette color per pixel in OKLab space. Returns (H, W) int
    array of palette indices."""
    lab_pal = rgb_array_to_oklab(
        np.array([hex_to_rgba(h)[:3] for h in palette_hex], dtype=float) / 255.0
    )
    arr = np.array(im.convert("RGB"), dtype=float) / 255.0
    lab = rgb_array_to_oklab(arr)
    d = ((lab[:, :, None, :] - lab_pal[None, None]) ** 2).sum(-1)
    return d.argmin(-1)


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


_BAYER = {
    "bayer2x2": np.array([[0, 2], [3, 1]]) / 4.0,
    "bayer4x4": np.array([
        [0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5],
    ]) / 16.0,
}


def dither_indices(im: Image.Image, palette_hex: list[str], pattern: str) -> np.ndarray:
    """Ordered (Bayer) dithering. Floyd-Steinberg is default-off and not
    offered — it's noise at 32x32, wrong for pixel art (CLAUDE.md #14)."""
    if pattern == "none":
        return quantize_to_palette(im, palette_hex)
    matrix = _BAYER[pattern]
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
