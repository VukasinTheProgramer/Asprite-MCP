"""OKLab color conversion and palette matching, shared by reference.py and the
Phase B conform/cleanup pipeline. RGB Euclidean distance picks visibly wrong
hues for quantization (CLAUDE.md #12) — OKLab is perceptually uniform, so
nearest-neighbor in this space tracks what a human would call "closest color".

Bjorn Ottosson's OKLab: https://bottosson.github.io/posts/oklab/
"""

import numpy as np

from .validation import hex_to_rgba

_M1 = np.array([
    [0.4122214708, 0.5363325363, 0.0514459929],
    [0.2119034982, 0.6806995451, 0.1073969566],
    [0.0883024619, 0.2817188376, 0.6299787005],
])
_M2 = np.array([
    [0.2104542553, 0.7936177850, -0.0040720468],
    [1.9779984951, -2.4285922050, 0.4505937099],
    [0.0259040371, 0.7827717662, -0.8086757660],
])
_M1_INV = np.linalg.inv(_M1)
_M2_INV = np.linalg.inv(_M2)


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.maximum(c, 0.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def rgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    """rgb: (..., 3) float in [0,1] sRGB. Returns (..., 3) OKLab."""
    lin = srgb_to_linear(rgb)
    lms = lin @ _M1.T
    lms_ = np.cbrt(np.maximum(lms, 0.0))
    return lms_ @ _M2.T


def oklab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """Inverse of rgb_to_oklab. Returns (..., 3) float clipped to [0,1] sRGB."""
    lms_ = lab @ _M2_INV.T
    lms = lms_ ** 3
    lin = lms @ _M1_INV.T
    return np.clip(linear_to_srgb(lin), 0.0, 1.0)


def nearest_palette_index(
    pixels_lab: np.ndarray,
    pal_lab: np.ndarray,
    weights: tuple[float, float, float] = (1.0, 1.0, 1.0),
    row_band: int = 64,
) -> np.ndarray:
    """pixels_lab (H,W,3), pal_lab (N,3) -> (H,W) indices.

    Weighting lightness higher than chroma (e.g. 1.3,1.0,1.0) usually reads
    better for pixel art: value structure carries shape, hue errors are more
    forgivable. Processed in row bands — the naive broadcast is H*W*N*3
    floats and exhausts memory on a 1024^2 image with a large palette.
    """
    w = np.asarray(weights)
    h = pixels_lab.shape[0]
    out = np.empty(pixels_lab.shape[:2], dtype=np.intp)
    for y0 in range(0, h, row_band):
        band = pixels_lab[y0:y0 + row_band]
        d = (((band[:, :, None, :] - pal_lab[None, None, :, :]) * w) ** 2).sum(-1)
        out[y0:y0 + row_band] = d.argmin(-1)
    return out


def palette_lab(palette_hex: list[str]) -> np.ndarray:
    """Hex palette -> (N,3) OKLab. Alpha is dropped: quantization is a color
    decision, alpha is thresholded separately."""
    return rgb_to_oklab(
        np.array([hex_to_rgba(h)[:3] for h in palette_hex], dtype=float) / 255.0
    )


def quantize_rgb(
    rgb: np.ndarray,
    palette_hex: list[str],
    weights: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> np.ndarray:
    """rgb (H,W,3) float [0,1] -> (H,W) palette indices, matched in OKLab."""
    return nearest_palette_index(rgb_to_oklab(rgb), palette_lab(palette_hex), weights)
