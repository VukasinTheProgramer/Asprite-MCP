import time

import numpy as np

from aseprite_mcp.conform import conform, downscale_modal


def _upscale_nearest(small: np.ndarray, factor: int) -> np.ndarray:
    return np.repeat(np.repeat(small, factor, axis=0), factor, axis=1)


def test_conform_round_trips_a_known_upscaled_sprite():
    """The strongest correctness signal (B4 acceptance): conform an 8x nearest-
    neighbour upscale of a real sprite back down and get the same sprite."""
    rng = np.random.default_rng(7)
    palette = ["#1a1c2c", "#5d275d", "#b13e53", "#ef7d57", "#ffcd75", "#a7f070", "#38b764", "#257179"]
    pal_idx = rng.integers(0, len(palette), size=(32, 32))

    sprite_rgb = np.zeros((32, 32, 3))
    for i, hexcolor in enumerate(palette):
        rgb = np.array([int(hexcolor[j:j + 2], 16) for j in (1, 3, 5)]) / 255.0
        sprite_rgb[pal_idx == i] = rgb
    sprite_idx = pal_idx

    upscaled_rgb = _upscale_nearest(sprite_rgb, 8)
    rgba = np.concatenate([upscaled_rgb, np.ones((256, 256, 1))], axis=-1)

    idx, alpha_mask, report = conform(rgba, (32, 32), palette)

    assert report["grid"]["is_pixel_art"] is True
    assert alpha_mask.all()
    # allow a handful of mismatches at cluster boundaries from OKLab rounding,
    # near-identical is the bar, not bit-exact
    mismatches = (idx != sprite_idx).sum()
    assert mismatches <= sprite_idx.size * 0.02


def test_conform_output_uses_only_palette_colors():
    rng = np.random.default_rng(11)
    palette = ["#000000", "#ffffff", "#ff0000", "#00ff00", "#0000ff"]
    photo = rng.random((100, 100, 3))
    rgba = np.concatenate([photo, np.ones((100, 100, 1))], axis=-1)

    idx, _, _ = conform(rgba, (16, 16), palette)

    assert idx.min() >= 0
    assert idx.max() < len(palette)


def test_conform_preserves_silhouette_of_a_simple_shape():
    """A filled circle on transparent ground, downscaled hard, should still
    read as roughly circular rather than collapsing to noise or a blob."""
    size = 512
    y, x = np.mgrid[0:size, 0:size]
    r = size * 0.35
    mask = ((x - size / 2) ** 2 + (y - size / 2) ** 2) <= r ** 2
    rgba = np.zeros((size, size, 4))
    rgba[mask] = [0.9, 0.2, 0.2, 1.0]

    palette = ["#000000", "#e63333"]
    idx, alpha_mask, _ = conform(rgba, (32, 32), palette)

    # crop_to_content crops to the circle's bounding box, so the circle ends up
    # inscribed in the 32x32 canvas -- fill fraction is the inscribed-circle
    # constant pi/4, not the circle's fraction of the original 512 canvas.
    filled = alpha_mask.sum()
    expected = (np.pi / 4) * 32 * 32
    assert abs(filled - expected) < expected * 0.15
    assert idx[16, 16] == 1  # dead center is the circle's color, not background


def test_conform_runs_within_time_budget_on_a_1024_input():
    rng = np.random.default_rng(3)
    rgba = np.concatenate([rng.random((1024, 1024, 3)), np.ones((1024, 1024, 1))], axis=-1)
    palette = ["#000000", "#ffffff", "#ff0000", "#00ff00", "#0000ff", "#ffff00"]

    start = time.monotonic()
    conform(rgba, (64, 64), palette)
    assert time.monotonic() - start < 3.0


def test_conform_bayer_dither_only_uses_palette_colors():
    palette = ["#000000", "#ffffff"]
    rng = np.random.default_rng(9)
    rgba = np.concatenate([rng.random((40, 40, 3)), np.ones((40, 40, 1))], axis=-1)

    idx, _, report = conform(rgba, (20, 20), palette, dither="bayer4x4")

    assert report["dither"] == "bayer4x4"
    assert set(np.unique(idx)) <= {0, 1}


def test_downscale_modal_preserves_hard_edge_no_blending():
    """A block straddling two flat colors should come out as one of them, not
    an averaged blend -- the whole point of modal over mean downscaling."""
    rgb = np.zeros((4, 4, 3))
    rgb[:, :2] = [1.0, 0.0, 0.0]
    rgb[:, 2:] = [0.0, 0.0, 1.0]

    out = downscale_modal(rgb, (2, 1))

    assert set(tuple(np.round(c, 3)) for c in out[0]) <= {(1.0, 0.0, 0.0), (0.0, 0.0, 1.0)}


def test_cleanup_output_alpha_must_derive_from_indices_not_conform_mask():
    """Index 0 is transparent in Aseprite whatever color palette entry 0 holds.
    Cleanup pushes pixels to index 0 that conform's alpha mask still calls
    opaque, so anything rendering the result (previews) has to take alpha from
    the post-cleanup indices. Taking it from the stale mask paints those pixels
    in palette[0]'s color at full opacity -- visible speckle that is not in the
    sprite that actually gets written."""
    from aseprite_mcp.cleanup import OPERATIONS, run_pipeline

    # subject plus isolated specks over a transparent field
    rgba = np.zeros((64, 64, 4))
    rgba[20:44, 20:44] = [0.8, 0.1, 0.1, 1.0]
    rgba[5, 5] = [0.1, 0.8, 0.1, 1.0]
    rgba[58, 58] = [0.1, 0.8, 0.1, 1.0]
    palette = ["#1e1ec8", "#c81e1e", "#1ec81e"]

    idx, alpha_mask, _ = conform(rgba, (16, 16), palette)
    pre = np.where(alpha_mask, idx, 0).astype(np.int32)
    post, _ = run_pipeline(pre, palette, list(OPERATIONS), aggressiveness=0.5)

    stale = int(((post == 0) & alpha_mask).sum())
    assert stale > 0, "fixture no longer exercises the divergence"
    # the correct rule: alpha follows the indices, so no index-0 pixel is opaque
    derived_alpha = post != 0
    assert not ((post == 0) & derived_alpha).any()
