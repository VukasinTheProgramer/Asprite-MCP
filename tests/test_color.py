import numpy as np

from aseprite_mcp.color import (
    extract_palette,
    nearest_palette_index,
    oklab_to_rgb,
    rgb_to_oklab,
)


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


def test_extract_palette_returns_requested_color_count():
    rng = np.random.default_rng(0)
    # four well-separated color blobs -- k-means should recover them
    blobs = np.array([[0.9, 0.1, 0.1], [0.1, 0.8, 0.2], [0.15, 0.2, 0.9], [0.95, 0.95, 0.9]])
    rgb = np.repeat(blobs, 400, axis=0).reshape(40, 40, 3)
    rgb = np.clip(rgb + rng.normal(0, 0.01, rgb.shape), 0, 1)

    pal = extract_palette(rgb, n_colors=4, seed=0)

    assert len(pal) == 4
    assert all(c.startswith("#") and len(c) == 7 for c in pal)


def test_extract_palette_is_deterministic():
    rng = np.random.default_rng(1)
    rgb = rng.random((32, 32, 3))
    assert extract_palette(rgb, 8, seed=3) == extract_palette(rgb, 8, seed=3)


def test_extract_palette_ignores_transparent_pixels():
    """A removed background must not spend palette entries on colors that will
    never be drawn."""
    rgb = np.zeros((10, 10, 3))
    rgb[:, :] = [0.0, 1.0, 0.0]  # green "background"
    rgb[4:6, 4:6] = [1.0, 0.0, 0.0]  # small red subject
    alpha = np.zeros((10, 10))
    alpha[4:6, 4:6] = 1.0

    pal = extract_palette(rgb, n_colors=2, alpha=alpha, seed=0)

    # only the red subject was visible, so no green should appear
    for c in pal:
        r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
        assert g < 128 or r > 128


def test_extract_palette_handles_fewer_unique_colors_than_requested():
    rgb = np.zeros((8, 8, 3))
    rgb[:, :4] = [1.0, 0.0, 0.0]
    rgb[:, 4:] = [0.0, 0.0, 1.0]

    pal = extract_palette(rgb, n_colors=16, seed=0)

    # index 0 is the reserved transparency slot; the art itself contributes at
    # most 2. Still never fabricates entries that claimed no pixels.
    assert 2 <= len(pal) <= 3
    assert 1 <= len(pal[1:]) <= 2


def test_extract_palette_reserves_index_0_for_transparency():
    """Aseprite renders index 0 transparent whatever color sits there. If
    k-means hands entry 0 a real color, every pixel quantized to it becomes a
    hole -- the B6 gate lost 36% of a pixel-art turtle this way, its black
    outline having clustered to index 0."""
    import numpy as np

    rgb = np.zeros((8, 8, 3))          # solid black: k-means' obvious cluster
    pal = extract_palette(rgb, n_colors=4, seed=0)
    assert pal[0] == "#ff00ff", "index 0 must be a placeholder, not art"
    assert "#000000" in pal[1:], "the real color still has to be in the palette"


def test_extract_palette_opt_out_still_gives_every_entry_to_the_art():
    import numpy as np

    rgb = np.zeros((8, 8, 3))
    pal = extract_palette(rgb, n_colors=4, seed=0, reserve_index_0=False)
    assert pal[0] != "#ff00ff"
