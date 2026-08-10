from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
    @mcp.prompt(name="sprite-character")
    def sprite_character(size: int, description: str, palette: str = "db16") -> str:
        """Draw a single character sprite, silhouette-first."""
        return f"""Create a {size}x{size} character sprite: {description}

Follow this order — do not skip steps:
1. create_sprite({size}, {size}, indexed, palette={palette})
2. get_ramp for each material (skin, cloth, metal) and name the indices
3. Block the SILHOUETTE only, in one flat mid-tone, using draw_grid.
   Check the preview. A good sprite is readable as a black silhouette.
4. Add base colors within the silhouette.
5. Shade: pick one light direction and state it. Use ramps, never
   arbitrary colors. Avoid pillow shading (uniform edge darkening).
6. Add a selective outline — darker version of the adjacent color,
   not pure black everywhere.
7. Add highlights sparingly — 2-3 pixels of the lightest tone.
8. Look at the preview and critique your own work against the description.
   Fix the single worst problem. Repeat twice."""

    @mcp.prompt(name="sprite-tileset")
    def sprite_tileset(tile_size: int, description: str, palette: str = "db16") -> str:
        """Draw a set of seamlessly-tiling tiles for a terrain or material."""
        return f"""Create a {tile_size}x{tile_size} tileset: {description}

Follow this order:
1. create_sprite({tile_size}, {tile_size}, indexed, palette={palette})
2. Establish the base texture in one or two flat tones first — check that
   pixels at x=0 could sit next to pixels at x={tile_size - 1} and read as
   continuous (same for y=0 / y={tile_size - 1}). This is what "seamless"
   means; check it now, before adding detail, not after.
3. Add texture/noise detail sparingly. Real materials read from silhouette
   and value contrast, not from busy per-pixel noise.
4. Use get_ramp for any shading — flat brightness ramps look artificial on
   a repeating surface.
5. export(format="png", scale=4) and mentally tile it 2x2 in your head from
   the preview. If a seam is visible, find which edge broke it and fix that
   edge specifically — don't redraw the whole tile."""

    @mcp.prompt(name="animate-walkcycle")
    def animate_walkcycle(size: int, description: str, frames: int = 4, palette: str = "db16") -> str:
        """Build a looping walk cycle: base pose, then per-frame variation."""
        return f"""Create a {frames}-frame {size}x{size} walk cycle: {description}

Follow this order:
1. create_sprite({size}, {size}, indexed, palette={palette})
2. Draw frame 1 as the CONTACT pose (both feet near the ground, weight
   centered) using draw_grid. This is the frame everything else is judged
   against — get it right before duplicating.
3. frames(action="duplicate", index=1) {frames - 1} times to get
   {frames} total frames, each a working copy of the contact pose.
4. Edit each frame in turn: the classic beat is contact -> passing (leg
   lifted, body dips) -> contact (opposite leg) -> passing (opposite leg).
   Adjust get_region_as_grid + draw_grid per frame; keep the silhouette's
   vertical bob subtle — big swings read as bouncing, not walking.
5. frames(action="set_duration", index=N, duration=0.1) for each frame —
   uniform timing unless you have a specific reason to vary it.
6. tags(action="add", name="walk", from_frame=1, to_frame={frames}) so the
   range is addressable as a unit.
7. export(format="gif") and check the loop — frame {frames} should read as
   flowing back into frame 1, not snapping."""

    @mcp.prompt(name="from-reference")
    def from_reference(image_path: str, size: int, palette: str = "db16") -> str:
        """Start from a reference photo/drawing instead of a blank canvas."""
        return f"""Turn the reference image at {image_path} into a {size}x{size} sprite.

Follow this order:
1. create_sprite({size}, {size}, indexed, palette={palette})
2. import_reference(image_path="{image_path}", mode="both") — this creates
   a locked 'reference' layer to glance at and an editable
   'reference_quantized' layer already in your palette as a starting point.
3. get_region_as_grid on the quantized layer to read its exact indices, and
   compare against the reference's silhouette. The auto-quantization gets
   colors close but not compositionally right — fix the biggest structural
   problem first (usually: silhouette read, then color blocking), not the
   smallest.
4. Refine on the quantized layer with draw_grid/draw_shape. This is editing
   a rough draft, not drawing from scratch — resist redrawing whole regions
   that are already close.
5. Once satisfied, layers(action="delete", name="reference") to remove the
   locked guide layer before exporting."""

    @mcp.prompt(name="palette-explore")
    def palette_explore(description: str) -> str:
        """Compare a few palette options for a concept before committing."""
        return f"""Before committing to a palette for "{description}", compare options:

1. create_sprite(32, 32, indexed, palette="pico8") — small test canvas
2. Pick 2-3 candidate presets or custom palettes suited to the mood
   (e.g. muted/desaturated for realism, high-saturation for a fantasy
   arcade look). set_palette for each in turn, or get_ramp from a
   single anchor color per candidate if not using a preset.
3. Block a tiny rough (silhouette + one shading pass) under each palette
   using draw_grid, comparing previews side by side in your own reasoning.
4. State which palette you're choosing and WHY — mood, contrast, the
   specific description above — before moving on to the real piece.
   Don't silently default to the first one tried."""
