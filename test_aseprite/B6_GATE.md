# B6 Gate — results

Run against `conform_image(palette="auto", palette_size=32, auto_cleanup=True, aggressiveness=0.5)`
through the real MCP tool path. Each row: original beside an 8× nearest-neighbour
view of the conformed sprite (`*_compare.png`), plus `*_after_1x.png` at native size.

Two passes are recorded: the first run, and a re-run after the systemic fixes
(aspect-preserving fit, palette-relative merge threshold, transparency invariant).

| # | Image | Target | Fitted | Silhouette | Palette | Edges | Detail | Pixel art? | First | **Final** |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | nature (aerial river, photo) | 128×128 | 128×126 | ✅ | ✅ | ✅ | ✅ | ✅ | 5/5 | **5/5** |
| 2 | pikachu (flat vector character) | 64×64 | 61×64 | ✅ | ✅ | ✅ | ✅ | ✅ | 4/5 | **5/5** |
| 3 | orb (soft-glow VFX) | 32×32 | 32×21 | ✅ | ✅ | ✅ | ✅ | ✅ | 1/5 | **5/5** |
| 4 | person (photo portrait) | 64×64 | 46×64 | ❌ | ✅ | ✅ | ✅ | ✅ | 3/5 | **4/5** |
| — | turtle (pixel-art control) | 32×32 | 32×32 | ❌ | ✅ | ❌ | ❌ | ✅ | void | **void** |

Colours surviving, first pass → final: orb **4 → 18**, person **19 → 30**,
pikachu 20 → 22. The orb went from four disconnected magenta dashes to a full
glowing disc; pikachu's detached ear reattached.

Person's remaining fail is the white shirt merging into the white backdrop —
`remove_background` flood-fills from the corners and the two are genuinely the
same colour. Documented limitation, not a defect; crop tighter or supply alpha.

## Decision

The plan's rule keys on the 64×64 character tier: *"4–5 criteria pass on image 2 →
the pipeline works. Continue to Phase C."*

**Pikachu scores 5/5 after the fixes (4/5 before) → PROCEED to Phase C**, with the caveats below.

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
alternating colours. The ear break was a genuine pipeline defect and is fixed; the grey is not ours.

## Defects found and fixed during the run

The first invalidated the whole first pass.

**1. `extract_palette` gave index 0 to real art.** Aseprite renders index 0 as
transparent whatever colour sits there. k-means had no reason to leave it alone,
so on the turtle it assigned `#000000` — the outline — and 36% of the sprite
became holes, taking everything the outline enclosed with it. Pikachu drew
`#fffffe` there and survived by luck. Fixed: entry 0 is now a reserved `#ff00ff`
placeholder, clusters computed on `n_colors - 1`.

**2. `thin_lines` punched holes in opaque art — four separate times.** It set
the dropped pixel of a 2px run to *transparent*, which is only right at a
silhouette edge; inside a filled region it makes the region see-through (1405px
of speckle across the nature photo). Each fix uncovered another case: the
neighbour being the same colour in a different component, then the neighbour
being transparency itself. It now never writes transparency at all — if neither
side offers a colour it leaves the pixel alone. The run-pipeline invariant below
is what caught cases 3 and 4; they would otherwise have shipped.

**3. `conform` stretched every subject to fill the canvas.** `downscale_modal`
resized straight to `target_size` regardless of aspect, so the 183×275 portrait
was squeezed into 64×64 and came out 1.5× too wide. `fit="contain"` is now the
default: aspect preserved, result centred, margin left transparent. Filling the
canvas was never the goal.

All have regression tests.

## The B4 tuning item — diagnosed, then fixed

**`merge_near_colors` used an absolute OKLab threshold**, so on a low-contrast
image it ate the entire subject. The orb's first-pass failure was wholly this:

| aggressiveness | distinct colours surviving | merged |
|---|---|---|
| conform output, before cleanup | 27 | — |
| 0.0 | 27 | 0px |
| 0.25 | 16 | 73px |
| **0.5 (default)** | **4** | **363px** |

Conform preserves the glow correctly. Cleanup destroys it. The threshold
(`0.01 + 0.04·a`, so 0.03 at default) is wider than the orb's entire colour
spread, because the image is dark and low-contrast end to end.

Conform preserved the glow correctly; cleanup destroyed it. The threshold is now
scaled to the palette's own median nearest-neighbour spacing, because
"near-duplicate" only means anything relative to how far apart the entries
normally sit. A well-spaced k-means palette now merges nothing at the default;
the orb keeps **18 colours instead of 4**, and no per-image tuning is needed.

## Reproduce

```
python b6_gate.py        # scratchpad; writes *_after_1x.png + b6_raw.json here
```

`b6_raw.json` holds the per-image `detect_grid` output, the full cleanup
per-operation pixel counts, and colour/opacity measurements.
