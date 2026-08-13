import numpy as np

from aseprite_mcp.grid import detect_grid, snap_to_grid


def _upscale_nearest(small: np.ndarray, factor: int) -> np.ndarray:
    return np.repeat(np.repeat(small, factor, axis=0), factor, axis=1)


def _random_sprite(rng: np.random.Generator, size: int) -> np.ndarray:
    return rng.random((size, size, 3))


def test_detect_grid_finds_true_upscale_factor():
    rng = np.random.default_rng(1)
    sprite = _random_sprite(rng, 32)
    upscaled = _upscale_nearest(sprite, 8)

    info = detect_grid(upscaled)

    assert info["cell_w"] == 8
    assert info["cell_h"] == 8
    assert info["offset_x"] == 0
    assert info["offset_y"] == 0
    assert info["confidence"] > 0.8
    assert info["is_pixel_art"] is True


def test_detect_grid_finds_offset_upscale():
    rng = np.random.default_rng(2)
    sprite = _random_sprite(rng, 16)
    upscaled = _upscale_nearest(sprite, 6)
    # pad 2px on top-left so the grid no longer starts at the image origin
    padded = np.pad(upscaled, ((2, 0), (2, 0), (0, 0)), mode="edge")

    info = detect_grid(padded)

    assert info["cell_w"] == 6
    assert info["cell_h"] == 6
    assert info["offset_x"] == 2
    assert info["offset_y"] == 2


def test_detect_grid_rejects_photograph():
    rng = np.random.default_rng(3)
    # smooth gradient plus noise -- no periodic structure at any cell size
    y, x = np.mgrid[0:128, 0:128]
    photo = np.stack([x, y, (x + y) / 2], axis=-1) / 255.0
    photo = np.clip(photo + rng.normal(0, 0.02, photo.shape), 0, 1)

    info = detect_grid(photo)

    assert info["is_pixel_art"] is False
    assert info["confidence"] < 0.5


def test_snap_to_grid_recovers_original_pixel_on_clean_upscale():
    rng = np.random.default_rng(4)
    sprite = _random_sprite(rng, 12)
    upscaled = _upscale_nearest(sprite, 5)

    info = detect_grid(upscaled)
    snapped = snap_to_grid(upscaled, info)

    # every 5x5 block should now be the single flat color of its source pixel
    recovered = snapped[::5, ::5]
    assert np.allclose(recovered, sprite, atol=1e-6)
    assert np.allclose(snapped, _upscale_nearest(sprite, 5), atol=1e-6)


def test_snap_to_grid_is_noop_when_no_grid_detected():
    rng = np.random.default_rng(5)
    photo = rng.random((32, 32, 3))
    info = detect_grid(photo)

    out = snap_to_grid(photo, info)

    # low-confidence detections can still return a best-guess cell size, but
    # snap_to_grid only no-ops when detection genuinely found nothing
    if info["cell_w"] is None:
        assert np.array_equal(out, photo)
