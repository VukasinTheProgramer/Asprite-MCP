# B6 Gate — results

Run against `conform_image(palette="auto", palette_size=32, auto_cleanup=True, aggressiveness=0.5)`
through the real MCP tool path. Each row: original beside an 8× nearest-neighbour
view of the conformed sprite (`*_compare.png`), plus `*_after_1x.png` at native size.

| # | Image | Target | Silhouette | Palette | Edges | Detail | Is it pixel art? | Score |
|---|---|---|---|---|---|---|---|---|
| 1 | nature (aerial river, photo) | 128×128 | ✅ | ✅ | ✅ | ✅ | ✅ | **5/5** |
| 2 | pikachu (flat vector character) | 64×64 | ❌ | ✅ | ✅ | ✅ | ✅ | **4/5** |
| 3 | orb (soft-glow VFX) | 32×32 | ❌ | ❌ | ❌ | ❌ | ✅ | **1/5** |
| 4 | person (photo portrait) | 64×64 | ❌ | ✅ | ✅ | ❌ | ✅ | **3/5** |
| — | turtle (pixel-art control) | 32×32 | ❌ | ✅ | ❌ | ❌ | ✅ | **void** |

## Decision

The plan's rule keys on the 64×64 character tier: *"4–5 criteria pass on image 2 →
the pipeline works. Continue to Phase C."*

**Pikachu scores 4/5 → PROCEED to Phase C**, with the caveats below.

Nature at 5/5 is the strongest result and the least expected one — a detailed
photograph downscaled 4.3× to 128×128 keeps its meander structure, sandbars and
forest/rock zoning legible. The two-stage LANCZOS→modal downscale is doing real work.

## Caveats on the verdict

**The turtle control is void, not failed.** The source carries `pngtree`
watermarks across the art. Those add off-grid edge energy that drowns the grid
signal: `detect_grid` returns `confidence=0.05, is_pixel_art=False`, so no grid
snap happens and the sprite gets ratio-downscaled like a photo. The algorithm is
not at fault — a clean synthetic 80× upscale of 25×25 art detects `cell=80` at
`confidence=1.0`. Re-run this control on unwatermarked pixel art before trusting
the "cleanup is near-no-op on clean art" risk-register item.

**Pikachu's input is contaminated too.** Its transparency was flattened to a
visible grey checkerboard before it reached the pipeline, which is what the grey
blocks on the right are. `remove_background` correctly refuses to flood across
alternating colours. The ear break is a genuine pipeline defect; the grey is not.

## Two defects found and fixed during the run

Both were blocking, and the first invalidated the whole first pass.

**1. `extract_palette` gave index 0 to real art.** Aseprite renders index 0 as
transparent whatever colour sits there. k-means had no reason to leave it alone,
so on the turtle it assigned `#000000` — the outline — and 36% of the sprite
became holes, taking everything the outline enclosed with it. Pikachu drew
`#fffffe` there and survived by luck. Fixed: entry 0 is now a reserved `#ff00ff`
placeholder, clusters computed on `n_colors - 1`.

**2. `thin_lines` punched holes in opaque art.** It set the dropped pixel of a
2px run to *transparent*, which is only correct when the run is a silhouette
edge. Inside a filled region it makes the region see-through — 1405px of speckle
across the nature photo. Fixed: the dropped pixel takes the colour just beyond
the run, falling back to transparent only at a real boundary. Nature went from
91% → 100% opaque.

Both have regression tests (`test_color.py`, `test_cleanup_b24.py`).

## One defect diagnosed, not fixed — this is the B4 tuning item

**`merge_near_colors` uses an absolute OKLab threshold**, so on a low-contrast
image it eats the entire subject. The orb's failure is wholly this:

| aggressiveness | distinct colours surviving | merged |
|---|---|---|
| conform output, before cleanup | 27 | — |
| 0.0 | 27 | 0px |
| 0.25 | 16 | 73px |
| **0.5 (default)** | **4** | **363px** |

Conform preserves the glow correctly. Cleanup destroys it. The threshold
(`0.01 + 0.04·a`, so 0.03 at default) is wider than the orb's entire colour
spread, because the image is dark and low-contrast end to end.

The fix is to scale the threshold to the palette's own spread rather than using
an absolute distance — an image whose colours span 0.02 OKLab should never merge
at 0.03. That is a judgement call about default behaviour, so it is flagged
rather than changed unilaterally.

Until then: **soft-glow VFX on dark backgrounds need `aggressiveness=0.0`.**

## Reproduce

```
python b6_gate.py        # scratchpad; writes *_after_1x.png + b6_raw.json here
```

`b6_raw.json` holds the per-image `detect_grid` output, the full cleanup
per-operation pixel counts, and colour/opacity measurements.
