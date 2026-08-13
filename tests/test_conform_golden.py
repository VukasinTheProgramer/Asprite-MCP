"""End-to-end conform regression on art with the properties that broke the B6
gate. Every earlier fixture was a checkerboard, a solid block, or np.random —
152 unit tests were green while the pipeline destroyed all five real images.

The three fixtures here each carry one dangerous property:
  outlined  — a dominant near-black outline, the colour k-means grabs first
  lowcon    — a dark low-contrast gradient, narrower than an absolute merge ΔE
  portrait  — a non-square subject, which a stretch-to-fill resize distorts

Goldens are written on first run and committed. Regenerate deliberately (delete
the file and re-run), never to make CI pass — CLAUDE.md is explicit about that.
"""

import numpy as np
import pytest

from aseprite_mcp.cleanup import OPERATIONS, run_pipeline
from aseprite_mcp.color import TRANSPARENT_INDEX, extract_palette
from aseprite_mcp.conform import conform, fit_size

GOLDEN = __import__("pathlib").Path(__file__).parent / "golden"


def _outlined() -> np.ndarray:
    """A filled shape inside a thick near-black outline, on transparency."""
    a = np.zeros((120, 120, 4))
    a[10:110, 10:110] = [0.05, 0.05, 0.06, 1.0]      # outline
    a[18:102, 18:102] = [0.55, 0.79, 0.36, 1.0]      # body
    a[40:60, 35:50] = [0.05, 0.05, 0.06, 1.0]        # eye
    a[40:60, 70:85] = [0.05, 0.05, 0.06, 1.0]
    return a


def _lowcon() -> np.ndarray:
    """A dark radial glow: the whole image spans a tiny OKLab distance."""
    yy, xx = np.mgrid[0:96, 0:96]
    r = np.sqrt((yy - 48.0) ** 2 + (xx - 48.0) ** 2) / 48.0
    a = np.zeros((96, 96, 4))
    a[..., 2] = np.clip(0.35 - 0.30 * r, 0.02, 1.0)   # blue, fading
    a[..., 0] = np.clip(0.18 - 0.16 * r, 0.01, 1.0)
    a[..., 3] = 1.0
    return a


def _portrait() -> np.ndarray:
    """Deliberately non-square: 60x100 subject on transparency."""
    a = np.zeros((100, 60, 4))
    a[5:95, 5:55] = [0.85, 0.72, 0.60, 1.0]
    a[20:45, 15:45] = [0.25, 0.15, 0.10, 1.0]
    return a


CASES = {"outlined": (_outlined, 32), "lowcon": (_lowcon, 32), "portrait": (_portrait, 32)}


def _run(rgba: np.ndarray, target: int) -> np.ndarray:
    pal = extract_palette(rgba[..., :3], 16, alpha=rgba[..., 3])
    idx, alpha, _ = conform(rgba, (target, target), pal)
    idx = np.where(alpha, idx, TRANSPARENT_INDEX).astype(np.int32)
    out, _ = run_pipeline(idx, pal, list(OPERATIONS), 0.5)
    return out


@pytest.mark.parametrize("name", sorted(CASES))
def test_conform_output_matches_golden(name):
    make, target = CASES[name]
    out = _run(make(), target)
    GOLDEN.mkdir(exist_ok=True)
    path = GOLDEN / f"{name}.npy"
    if not path.exists():
        np.save(path, out)
        pytest.skip(f"wrote new golden {path.name} — inspect it, then commit")
    np.testing.assert_array_equal(out, np.load(path), err_msg=f"{name} output changed")


def test_outline_colour_survives_conform():
    """The regression that erased 36% of a sprite: the outline is the densest
    colour, so k-means reaches for it first. If it lands on the reserved index
    it becomes holes and takes everything it encloses with it."""
    out = _run(_outlined(), 32)
    interior = out[8:24, 8:24]
    assert (interior != TRANSPARENT_INDEX).all(), "holes opened inside the shape"


def test_low_contrast_art_is_not_collapsed_to_a_few_colours():
    """merge_near_colors used an absolute OKLab threshold wider than this
    image's entire colour spread, taking 27 surviving colours down to 4."""
    out = _run(_lowcon(), 32)
    assert len(set(out.flatten().tolist())) >= 6


def test_non_square_subject_keeps_its_aspect_ratio():
    """Filling the canvas is not the goal. A 60x100 subject forced into a
    square comes back stretched 1.67x wide unless the fit is preserved."""
    rgba = _portrait()
    pal = extract_palette(rgba[..., :3], 16, alpha=rgba[..., 3])
    _, alpha, report = conform(rgba, (32, 32), pal)
    fw, fh = report["fitted_size"]
    assert (fw, fh) == fit_size(50, 90, 32, 32)
    assert fw < fh, "a portrait subject must stay taller than it is wide"

    ys, xs = np.nonzero(alpha)
    assert (xs.max() - xs.min() + 1) < (ys.max() - ys.min() + 1)


def test_stretch_remains_available_for_callers_that_want_it():
    rgba = _portrait()
    pal = extract_palette(rgba[..., :3], 16, alpha=rgba[..., 3])
    _, _, report = conform(rgba, (32, 32), pal, fit="stretch")
    assert report["fitted_size"] == (32, 32)
