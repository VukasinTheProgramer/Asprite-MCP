# Pixel art craft guide

The default output of an LLM asked to draw pixel art is bad in specific, nameable ways. This guide
names them. Read it before drawing, and check your work against it before calling a piece finished.

## Readable silhouette

Block the shape in one flat mid-tone before adding any color or shading (`draw_grid` with a single
index). If it doesn't read as a recognizable shape when solid black, no amount of shading will fix
it — fix the silhouette first, always. This is the single highest-leverage step: get it right and
everything after is refinement, get it wrong and everything after is wasted.

## One consistent light source

Pick a single light direction (upper-left is the most common convention) and state it before
shading. Every highlight and shadow in the piece must be consistent with that one direction. Mixed
light directions are one of the most common tells of unplanned, undirected shading.

## Hue-shifted ramps, not brightness ramps

Shadows are not "the same color, darker" — they shift toward blue/purple and desaturate slightly.
Highlights are not "the same color, lighter" — they shift toward yellow and desaturate slightly at
the extreme. Flat brightness ramps (same hue, only lightness changed) read as cheap and flat. Use
`get_ramp` rather than picking shading colors by eye.

## Selective outlining, not full black outlines

A uniform black outline around every shape flattens the piece and fights the palette. Prefer a
darker, hue-shifted version of the adjacent fill color for most of the outline, reserving pure black
(or near-black) only for silhouette edges against a very different background.

## Avoid pillow shading

Pillow shading — uniformly darkening every edge of a shape regardless of the stated light
direction — is the most common failure mode for shading that ignores the "one consistent light
source" rule above. If every edge of a round shape gets darker toward its boundary regardless of
which side faces the light, that's pillow shading. Fix it by re-deriving shadow placement from the
stated light direction, not from "edges are dark."

## No orphan pixels or noise

A single stray pixel of a color that appears nowhere else nearby, especially after downscaling a
reference image, reads as noise rather than detail. Clean these up — `import_reference` does this
automatically for imported images, but hand-placed pixels need the same discipline.

## Use few colors deliberately

More colors is not more detail. A tightly curated 8-16 color palette with well-chosen ramps reads
better than a loosely-chosen 40-color palette. Constraint is a feature of the medium, not a
limitation to work around.

## Anti-aliasing only at low-contrast joins

Selective anti-aliasing — a single intermediate-tone pixel at a joint between two similar colors —
can soften a harsh diagonal. Applying it everywhere, or at high-contrast edges, produces blur instead
of the crisp readability that makes pixel art pixel art. When in doubt, leave the edge hard.

## Consistent pixel density

Every part of a sprite should read at the same implied "resolution" — don't draw a face with
single-pixel detail next to a torso blocked in with 4-pixel-wide flat regions. Mixed density inside
one sprite is one of the more subtle but consistent tells of inconsistent, unplanned drawing.

## Readable at 1x, not just zoomed

The preview you're shown is upscaled for your benefit — the end viewer often will not see it that
large. Periodically judge the piece as if seen small: does the silhouette and the primary color
block still read? If a detail only makes sense zoomed in, it's not doing its job at the sprite's
actual size.
