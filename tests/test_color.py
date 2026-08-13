import numpy as np

from aseprite_mcp.color import nearest_palette_index, oklab_to_rgb, rgb_to_oklab


def test_oklab_round_trip():
    rng = np.random.default_rng(0)
    rgb = rng.random((5, 5, 3))
    back = oklab_to_rgb(rgb_to_oklab(rgb))
    assert np.allclose(rgb, back, atol=1e-6)


def test_nearest_palette_index_exact_colors():
    palette = np.array([[0, 0, 0], [1, 1, 1], [1, 0, 0]], dtype=float)
    pal_lab = rgb_to_oklab(palette)
    # a 2x3 image built from exactly the palette colors, out of order
    pixels = np.array([[palette[2], palette[0]], [palette[1], palette[1]], [palette[0], palette[2]]])
    idx = nearest_palette_index(rgb_to_oklab(pixels), pal_lab)
    assert idx.tolist() == [[2, 0], [1, 1], [0, 2]]


def test_nearest_palette_index_row_band_matches_full():
    rng = np.random.default_rng(1)
    pixels = rng.random((10, 10, 3))
    palette = rng.random((6, 3))
    pal_lab = rgb_to_oklab(palette)
    pixels_lab = rgb_to_oklab(pixels)
    full = nearest_palette_index(pixels_lab, pal_lab, row_band=10_000)
    banded = nearest_palette_index(pixels_lab, pal_lab, row_band=3)
    assert np.array_equal(full, banded)
