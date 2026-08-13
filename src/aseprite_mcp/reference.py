"""Reference image loading and background isolation. Pure Pillow/numpy —
nothing here touches Aseprite.

This module used to carry a whole second pipeline: crop, downscale, quantize,
dither, orphan removal. Phase B replaced every one of those with a better
implementation (conform.py's grid detection and modal downscale, cleanup.py's
orphan removal), but the old copies stayed, and `import_reference` kept calling
them. The two paths then drifted — the index-0 fix landed on `conform_image` and
missed this one entirely, so importing against pico8 erased 30% of a sprite's
opaque pixels long after the bug was "fixed".

The superseded copies are deleted rather than deprecated. A duplicate pipeline
that still runs is a duplicate pipeline that will diverge again.
"""

import numpy as np
from PIL import Image, ImageOps

from .color import rgb_to_oklab

rgb_array_to_oklab = rgb_to_oklab  # back-compat alias for existing callers/tests


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
