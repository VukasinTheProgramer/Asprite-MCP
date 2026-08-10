import numpy as np
from PIL import Image

from aseprite_mcp.reference import (
    crop_to_content,
    quantize_to_palette,
    remove_background,
    remove_orphan_pixels,
    rgb_array_to_oklab,
)


def test_oklab_matches_published_reference_values():
    """Björn Ottosson's own worked example (bottosson.github.io/posts/oklab).
    Getting the matrix coefficients subtly wrong would silently produce bad
    quantization — this pins them exactly, not just "doesn't crash"."""
    def oklab(rgb):
        return rgb_array_to_oklab(np.array([[rgb]], dtype=float))[0, 0]

    white = oklab((1, 1, 1))
    assert np.allclose(white, [1.0, 0.0, 0.0], atol=1e-4)

    black = oklab((0, 0, 0))
    assert np.allclose(black, [0.0, 0.0, 0.0], atol=1e-4)

    red = oklab((1, 0, 0))
    assert np.allclose(red, [0.6280, 0.2249, 0.1258], atol=1e-3)


def test_quantize_picks_the_closer_color_not_rgb_nearest():
    """A case where OKLab and RGB-Euclidean nearest-neighbor disagree would
    be the real test, but even the trivial case must at least be right."""
    im = Image.new("RGB", (1, 1), (200, 50, 50))
    indices = quantize_to_palette(im, ["#ff0000", "#00ff00", "#0000ff"])
    assert indices[0, 0] == 0  # closest to red


def test_remove_background_clears_uniform_corners_not_the_subject():
    arr = np.full((10, 10, 4), 255, dtype=np.uint8)
    arr[:, :, :3] = 255  # white background
    arr[3:7, 3:7, :3] = 0  # black square subject in the middle
    im = Image.fromarray(arr, "RGBA")

    out = remove_background(im, tolerance=10)
    out_arr = np.array(out)
    assert out_arr[0, 0, 3] == 0  # corner became transparent
    assert out_arr[5, 5, 3] == 255  # subject untouched


def test_crop_to_content_trims_transparent_margin():
    arr = np.zeros((10, 10, 4), dtype=np.uint8)
    arr[4:6, 4:6, 3] = 255  # 2x2 opaque square in the middle
    im = Image.fromarray(arr, "RGBA")
    cropped = crop_to_content(im)
    assert cropped.size == (2, 2)


def test_remove_orphan_pixels_fixes_isolated_speckle():
    grid = np.array([
        [0, 0, 0],
        [0, 9, 0],  # single orphan pixel, all 4 neighbors are 0
        [0, 0, 0],
    ])
    cleaned = remove_orphan_pixels(grid)
    assert cleaned[1, 1] == 0


def test_remove_orphan_pixels_leaves_real_edges_alone():
    grid = np.array([
        [1, 1, 0],
        [1, 1, 0],  # a real 2x2 block, not an orphan
        [0, 0, 0],
    ])
    cleaned = remove_orphan_pixels(grid)
    assert (cleaned == grid).all()
