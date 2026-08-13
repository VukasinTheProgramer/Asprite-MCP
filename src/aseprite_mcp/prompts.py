"""Model-facing workflows.

Rewritten for the hybrid flow (upgrade-plan Phase E): anchor on a style project,
get pixels onto the canvas (drawn or conformed from a reference), repair
deterministically with `cleanup`, then critique. The v1 prompts walked the model
through drawing pixel by pixel and never mentioned `style`, `conform_image` or
`cleanup` — so the Phase A/B tools existed but nothing pointed at them.

Diffusion-backed generation (plan C2) is deliberately absent: there is no
backend wired up, and a prompt describing a tool that doesn't exist is worse
than no prompt.
"""

from mcp.server.mcpserver import MCPServer

_ANCHOR = """1. style(action="get") to load the project's palette, ramps and light
   direction. If it errors, no project is active — either
   style(action="set_active", project=...) or create one with
   style(action="create", project=..., palette="db16"). Do this first: with a
   project active you stop choosing a palette and canvas size per sprite, which
   is what keeps a set looking like a set."""

_CLEANUP = """cleanup() — deterministic repair for anti-aliasing fringes, orphan
   pixels and irregular diagonals. It is a no-op on already-clean art, so it
   costs one call to find out. Read the per-operation pixel counts it reports: a
   large remove_aa count on art you drew by hand means it is too aggressive,
   so lower `aggressiveness` and re-run."""


def register(mcp: MCPServer) -> None:
    @mcp.prompt(name="style-setup")
    def style_setup(project: str, description: str, palette: str = "db16") -> str:
        """Establish a project's visual language before making any assets."""
        return f"""Set up the style bible for "{project}": {description}

Everything else depends on this, so spend the calls here rather than
rediscovering the palette on every sprite.

1. Decide the palette. Either a bundled preset ({palette} is a reasonable
   default) or an explicit hex list if the concept needs specific colours.
   get_ramp(base_color=..., steps=5) gives a hue-shifted ramp per material —
   shadows shifted cool, highlights warm — which reads far better than a
   brightness slider. Do this for each major material in the description.
2. style(action="create", project="{project}", palette=...) — ramps and outline
   indices are derived from the palette for you. The returned swatch strip shows
   you what you actually got; look at it rather than trusting the hex list.
3. style(action="update", ...) to set anything the derivation got wrong:
   - canvas_defaults per asset type (character/item/tile/portrait/vfx)
   - light_source, if not upper_left_45
   - roles, naming indices semantically ({{3: "bark_shadow"}}) so later work can
     refer to the choice rather than re-deriving it
4. style(action="get") once more and state, in one line, what this project's
   look IS — palette mood, light direction, outline treatment. That sentence is
   what you check later work against.

Entry 0 of any palette is reserved for transparency, so never assign it a role."""

    @mcp.prompt(name="sprite-character")
    def sprite_character(name: str, description: str, size: int = 32) -> str:
        """Draw a character sprite, silhouette-first, on the project's style."""
        return f"""Create a {size}x{size} character sprite "{name}": {description}

{_ANCHOR}
2. create_sprite(name="{name}", asset_type="character") — with a project active
   this takes the canvas size and palette automatically. Pass width/height
   explicitly only if you need to override the project default.
3. Block the SILHOUETTE only, one flat mid-tone, with draw_grid. Check the
   preview. A good sprite is readable as a black silhouette — if it isn't
   recognisable now, no amount of shading will save it. Fix it here.
4. Fill base colours inside the silhouette, one per material.
5. Shade using the project's ramps (style's `ramps` field, dark -> light) and
   its light_source. State the light direction out loud before you start.
   Never pick arbitrary colours; never pillow-shade (uniform edge darkening).
6. Selective outline: a darker version of the adjacent colour, not pure black
   everywhere. The project's outline_indices are the darks to reach for.
7. Highlights last, sparingly — a few pixels of the lightest ramp step.
8. {_CLEANUP}
9. Look at the preview and critique against the description. Fix the single
   worst problem, then repeat once. Silhouette problems outrank colour
   problems; readability at 1x outranks detail."""

    @mcp.prompt(name="sprite-from-reference")
    def sprite_from_reference(image_path: str, name: str, size: int = 32) -> str:
        """Convert a reference image into a sprite with conform_image."""
        return f"""Turn the reference at {image_path} into a {size}x{size} sprite "{name}".

Use conform_image, not import_reference — it detects and corrects a soft or
upscaled grid, downscales by majority-colour-per-cell instead of blurring, and
chains the cleanup pass automatically.

{_ANCHOR}
2. detect_grid(image_path="{image_path}") first. If `is_pixel_art` is true, the
   source is pixel art (or a clean upscale of it) and cell_w tells you its true
   resolution — a cell of 8 on a 256px image means the real art is 32x32, so
   target that rather than {size}x{size}. If it is false, the source is a photo
   or a smooth render and any target is fair game.
3. create_sprite(name="{name}", asset_type="character") — the canvas must equal
   the target_size you are about to conform to.
4. conform_image(image_path="{image_path}", target_size=[{size}, {size}])
   - Omit `palette` to lock to the active project. That is the whole point of
     having one. Pass palette="auto" only when you want to KEEP the reference's
     own colours rather than adopt the project's.
   - remove_background=True if the subject sits on a scene or backdrop. It
     flood-fills inward from the corners, so it will not clear a background
     that is the same colour as the subject — crop tighter if that happens.
   - The result is fitted to the canvas preserving aspect ratio, centred, with
     transparent margin. Filling the canvas is not the goal.
   - Try import_to_sprite=False first to inspect the before/after without
     committing anything.
5. Read the report it returns: the fitted size, whether the grid was snapped or
   ratio-downscaled, and the per-operation cleanup counts.
6. Now refine BY HAND. Conform gets you a correct, on-palette starting point,
   not a finished sprite. get_region_as_grid to read exact indices, then
   draw_grid to fix. Work biggest problem first — silhouette read, then colour
   blocking, then detail. Resist redrawing regions that are already close."""

    @mcp.prompt(name="sprite-tileset")
    def sprite_tileset(name: str, description: str, tile_size: int = 16) -> str:
        """Draw a seamlessly-tiling terrain or material tile."""
        return f"""Create a {tile_size}x{tile_size} tile "{name}": {description}

{_ANCHOR}
2. create_sprite(name="{name}", asset_type="tile")
3. Base texture in one or two flat tones FIRST. Check that pixels at x=0 could
   sit beside pixels at x={tile_size - 1} and read as continuous, same for
   y=0 / y={tile_size - 1}. That is what seamless means — verify it now, while
   the tile is simple, not after you have added detail.
4. Add texture sparingly, using the project's ramps. Materials read from value
   contrast and silhouette, not from busy per-pixel noise. At this size, three
   tones of deliberate variation beat thirty of speckle.
5. {_CLEANUP}
6. export(format="png", scale=4) and tile it 2x2 in your reasoning. If a seam
   shows, identify WHICH edge broke it and fix that edge — do not redraw the
   tile."""

    @mcp.prompt(name="animate-walkcycle")
    def animate_walkcycle(name: str, description: str, frames: int = 4, size: int = 32) -> str:
        """Build a looping walk cycle from one key pose."""
        return f"""Create a {frames}-frame {size}x{size} walk cycle "{name}": {description}

Frames are transformations of a master pose, never independent redraws — that
is what keeps the character on-model across the loop.

{_ANCHOR}
2. create_sprite(name="{name}", asset_type="character")
3. Draw frame 1 as the CONTACT pose (both feet near the ground, weight centred).
   Every other frame is judged against this one, so finish it before moving on.
4. frames(action="duplicate", index=1), {frames - 1} times.
5. Edit each copy in turn. The beat is contact -> passing (leg lifted, body
   dips) -> contact (opposite leg) -> passing (opposite). Keep the vertical bob
   subtle; large swings read as bouncing, not walking. get_region_as_grid to
   read a frame, draw_grid to change it.
6. frames(action="set_duration", index=N, duration=0.1) for each — uniform
   timing unless you have a specific reason otherwise.
7. tags(action="add", name="walk", from_frame=1, to_frame={frames})
8. export(format="gif") and check that frame {frames} flows back into frame 1
   rather than snapping. If it snaps, the two contact poses are probably too
   similar — differentiate the arm positions."""

    @mcp.prompt(name="palette-explore")
    def palette_explore(description: str, project: str = "untitled") -> str:
        """Compare palette options, then commit the winner to a style project."""
        return f"""Choose a palette for "{description}", then commit it.

This ends with a style project, not just an opinion — an unrecorded decision
gets silently re-made differently on the next sprite.

1. Pick 2-3 candidates suited to the mood: muted and desaturated reads
   realistic, high-saturation reads arcade/fantasy. Bundled presets are pico8,
   db16, sweetie16, gameboy. For a custom set, get_ramp from one anchor colour
   per material gives a coherent ramp rather than hand-picked hexes.
2. create_sprite(name="palette_test", width=32, height=32) as a scratch canvas.
3. For each candidate: set_palette, then draw the same small rough — silhouette
   plus one shading pass — with draw_grid. Compare the previews directly.
   Judge on value contrast at 1x, not on which looks prettiest zoomed in.
4. State which you are choosing and WHY, referring to the mood and the
   description above. Do not silently default to the first one tried.
5. style(action="create", project="{project}", palette=<the winner>) so every
   later asset inherits it."""
