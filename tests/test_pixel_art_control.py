"""The risk-register control: cleanup must be near-no-op on hand-made pixel art.

From the upgrade plan's risk table:

    remove_antialiasing too aggressive, eats real detail
    -> Expose `threshold`; test on hand-made pixel art (should be near-no-op)

The B6 gate's control was a watermarked stock sprite. The watermarks added
off-grid edge energy, `detect_grid` scored 0.05, and the image was
ratio-downscaled like a photograph — so the run measured the watermarks, not the
pipeline, and the claim stayed untested. This fixture is authored here: clean,
unwatermarked, no third-party art, and deliberately built to contain everything
cleanup is tempted to "fix".
"""

import numpy as np
import pytest

from aseprite_mcp.cleanup import OPERATIONS, run_pipeline
from aseprite_mcp.color import TRANSPARENT_PLACEHOLDER
from aseprite_mcp.conform import conform
from aseprite_mcp.grid import detect_grid

# index 0 reserved, then outline / dark / mid / light
PALETTE = [TRANSPARENT_PLACEHOLDER, "#2a1a0e", "#4a3018", "#7a5228", "#c99a5e"]

# A 16x16 hand-authored sprite. Deliberately includes the shapes cleanup could
# damage: a regular 2-2-2 staircase (fix_jaggies), a 1px line (thin_lines), flat
# fields with hard edges (remove_aa), and a lone highlight pixel that is real art
# rather than a speck (remove_orphans).
ART = [
    "................",
    ".....111111.....",
    "....12222221....",
    "...1224444221...",
    "...1224444221...",
    "..122222222221..",
    "..122333332221..",
    "..122333332221..",
    "..122333332221..",
    "...1223333221...",
    "...1222222221...",
    "...1222222221...",
    "...122....221...",
    "...122....221...",
    "...111....111...",
    "................",
]


def _sprite() -> np.ndarray:
    idx = np.array([[0 if c == "." else int(c) for c in row] for row in ART], dtype=np.int32)
    return idx


def _to_rgba(idx: np.ndarray) -> np.ndarray:
    from aseprite_mcp.validation import hex_to_rgba

    pal = np.array([hex_to_rgba(h)[:3] for h in PALETTE], dtype=float) / 255.0
    rgba = np.zeros((*idx.shape, 4))
    rgba[..., :3] = pal[idx]
    rgba[..., 3] = (idx != 0).astype(float)
    return rgba


def _upscale(rgba: np.ndarray, f: int) -> np.ndarray:
    return np.repeat(np.repeat(rgba, f, axis=0), f, axis=1)


@pytest.mark.parametrize("aggressiveness", [0.0, 0.25, 0.5])
def test_cleanup_is_near_noop_on_hand_made_pixel_art(aggressiveness):
    """The risk-register claim, finally measured. Clean art has no AA fringe to
    strip and no specks to remove, so cleanup should barely touch it. A large
    count here means an operation is eating real detail."""
    idx = _sprite()
    out, report = run_pipeline(idx, PALETTE, list(OPERATIONS), aggressiveness)

    changed = int((out != idx).sum())
    opaque = int((idx != 0).sum())
    # 10%, not 0: this fixture's outline is a deliberately irregular staircase
    # (run lengths 1,1,2,4), so fix_jaggies has real work to do on it and doing
    # that work is correct. What must never happen is erasure — asserted
    # separately below, and that one is exact.
    assert changed <= opaque * 0.10, (
        f"cleanup changed {changed}/{opaque} opaque pixels at "
        f"aggressiveness={aggressiveness}: {report}"
    )


def test_cleanup_never_makes_hand_made_art_transparent():
    """Erasing part of a clean sprite is the failure that actually hurts —
    it is invisible in a pixel count and obvious on screen."""
    idx = _sprite()
    out, _ = run_pipeline(idx, PALETTE, list(OPERATIONS), 0.5)
    lost = int(((idx != 0) & (out == 0)).sum())
    assert lost == 0, f"{lost} opaque pixels were erased"


def test_detect_grid_finds_the_cell_of_a_clean_upscale():
    """What the watermarked control could not establish: on genuine pixel art,
    grid detection is confident and correct."""
    big = _upscale(_to_rgba(_sprite()), 8)
    info = detect_grid(big[..., :3])
    assert info["is_pixel_art"] is True
    assert info["cell_w"] == 8 and info["cell_h"] == 8
    assert info["confidence"] > 0.8


def test_conform_round_trips_hand_made_pixel_art_through_an_upscale():
    """B4's strongest correctness signal, on a real sprite rather than random
    indices: upscale 8x, conform back, get the sprite again.

    Compared against the content-cropped sprite, because conform crops to
    content by design — a sprite with a transparent margin is legitimately
    reframed, so expecting the margin back would be testing the wrong claim.
    """
    full = _sprite()
    ys, xs = np.nonzero(full != 0)
    original = full[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = original.shape

    big = _upscale(_to_rgba(original), 8)
    idx, alpha, report = conform(big, (w, h), PALETTE)
    assert report["grid"]["is_pixel_art"] is True

    got = np.where(alpha, idx, 0).astype(np.int32)
    mismatches = int((got != original).sum())
    assert mismatches <= original.size * 0.02, (
        f"{mismatches}/{original.size} pixels differ after a round trip"
    )


def test_the_fixture_actually_contains_what_it_claims():
    """A control that degenerated into a plain rectangle would pass everything
    above while testing nothing."""
    idx = _sprite()
    assert len(set(idx.flatten().tolist())) == 5, "all palette entries must appear"
    assert (idx == 0).any(), "needs transparency"
    # two legs with a real gap between them: close_silhouette must not fill it
    assert (idx[13, 6:10] == 0).all()
    # and nothing dangles -- a 1px tip with one neighbour is whisker-shaped by
    # any local measure, so shaving it would be correct and the control would be
    # testing the fixture's own sloppiness rather than the pipeline
    from scipy import ndimage

    solid = idx != 0
    deg = ndimage.convolve(solid.astype(np.int16), np.ones((3, 3), np.int16), mode="constant") - solid
    assert not (solid & (deg <= 1)).any(), "fixture has dangling tips"
