import numpy as np
from PIL import Image

from aseprite_mcp.color import quantize_rgb
from aseprite_mcp.reference import remove_background, rgb_array_to_oklab

# reference.py's crop_to_content / downscale / quantize_to_palette /
# dither_indices / remove_orphan_pixels are gone: Phase B superseded every one
# of them and the surviving copies let the two pipelines drift apart. Their
# behaviour is covered by test_conform.py (cropping, downscale) and
# test_cleanup.py (orphan removal) against the implementations that actually
# run now.


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
    be the real test, but even the trivial case must at least be right.
    Repointed at color.quantize_rgb, which is what runs now."""
    arr = np.array([[[200, 50, 50]]], dtype=float) / 255.0
    indices = quantize_rgb(arr, ["#ff0000", "#00ff00", "#0000ff"])
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
