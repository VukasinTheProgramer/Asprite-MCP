import colorsys

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer

from ..deps import Bridge, Session
from ..errors import ToolError
from ..history import push_snapshot
from ..palettes import load_preset
from ..render import preview_image, render_palette_swatch, render_preview
from ..style import normalize_hex
from ..validation import hex_to_rgba, lua_str

_MAX_PALETTE = 256


def _shift_ramp(base_hex: str, steps: int, hue_shift: float) -> list[str]:
    """Darken toward black + shift hue cool for shadows, lighten toward white
    + shift hue warm for highlights, compressing saturation at the extremes —
    the standard technique that makes shading look intentional rather than a
    brightness slider (§7.3)."""
    r, g, b, a = hex_to_rgba(base_hex)
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)

    dark_n = steps // 2
    light_n = steps - dark_n - 1
    ramp: list[tuple[float, float, float]] = []

    for i in range(dark_n, 0, -1):
        t = i / dark_n
        ramp.append(((h - (hue_shift / 360) * t) % 1.0, l * (1 - 0.7 * t), s * (1 - 0.3 * t)))
    ramp.append((h, l, s))
    for i in range(1, light_n + 1):
        t = i / light_n if light_n else 0
        ramp.append(((h + (hue_shift / 360) * t) % 1.0, l + (1 - l) * 0.7 * t, s * (1 - 0.3 * t)))

    out = []
    for hh, ll, ss in ramp:
        rr, gg, bb = colorsys.hls_to_rgb(hh, max(0.0, min(1.0, ll)), max(0.0, min(1.0, ss)))
        out.append(f"#{round(rr * 255):02x}{round(gg * 255):02x}{round(bb * 255):02x}")
    return out


def register(mcp: MCPServer) -> None:
    @mcp.tool(structured_output=False)
    def set_palette(
        bridge: Bridge,
        session: Session,
        palette: str | list[str],
        names: dict[int, str] | None = None,
        preserve_indices: bool = False,
        sprite: str | None = None,
        preview: bool = True,
    ) -> list[str | MCPImage]:
        """Set the sprite palette from a preset name or explicit hex list.

        Bundled presets: pico8, db16, sweetie16, gameboy. `names` lets you
        label indices semantically ({3: "skin_shadow"}) so later drawing calls
        can reference the choice by name in your own reasoning. Index 0 is
        always transparent — see get_region_as_grid's docstring — so it's
        never worth naming.

        `preserve_indices=True` only appends new colors past the sprite's
        current palette length, leaving existing indices (and therefore
        existing pixels) untouched. False replaces the whole palette outright.
        """
        path = session.resolve_sprite(sprite)

        if isinstance(palette, str):
            colors = load_preset(palette)["colors"]
        else:
            colors = palette
        if not colors:
            raise ToolError(code="empty_palette", message="Palette must have at least one color.")
        if len(colors) > _MAX_PALETTE:
            raise ToolError(
                code="palette_too_large",
                message=f"{len(colors)} colors given; Aseprite indexed palettes cap at {_MAX_PALETTE}.",
            )
        for c in colors:
            hex_to_rgba(c)  # raises invalid_hex_color with the offending value


        color_lua = ",".join(
            f"Color{{r={r},g={g},b={b},a={a}}}" for r, g, b, a in (hex_to_rgba(c) for c in colors)
        )

        # Read the FINAL on-disk palette back in the same call, rather than
        # trusting our own inputs for the table/swatch — with preserve_indices,
        # only indices past the old size actually change, so "the colors we
        # asked for" and "what's actually there now" can disagree (index 0/1
        # keep their old color if the palette was already that long).
        read_back = (
            "local hex = {}\n"
            "for i = 0, #pal - 1 do\n"
            "  local c = pal:getColor(i)\n"
            '  hex[i+1] = string.format("#%02x%02x%02x", c.red, c.green, c.blue)\n'
            "end\n"
            "return { size = #pal, hex = hex }"
        )
        if preserve_indices:
            lua = (
                f"local spr = J.sprite({lua_str(path)})\n"
                "local pal = spr.palettes[1]\n"
                "local old_size = #pal\n"
                f"local new_colors = {{{color_lua}}}\n"
                "local new_size = math.max(old_size, #new_colors)\n"
                "pal:resize(new_size)\n"
                "for i = 1, #new_colors do\n"
                "  if (i - 1) >= old_size then pal:setColor(i - 1, new_colors[i]) end\n"
                "end\n"
                "J.save(spr)\n" + read_back
            )
        else:
            lua = (
                f"local spr = J.sprite({lua_str(path)})\n"
                f"local new_colors = {{{color_lua}}}\n"
                "local pal = Palette(#new_colors)\n"
                "for i, c in ipairs(new_colors) do pal:setColor(i - 1, c) end\n"
                "spr:setPalette(pal)\n"
                "J.save(spr)\n" + read_back
            )
        push_snapshot(session, path)
        result = bridge.execute(lua)
        final_colors: list[str] = result["hex"]

        handle = session.sprites.get(path)
        if handle is not None:
            handle.palette = final_colors
            if names:
                handle.palette_names.update(names)

        table_lines = ["| idx | hex | name |", "|---|---|---|"]
        display_names = handle.palette_names if handle else (names or {})
        for i, c in enumerate(final_colors):
            table_lines.append(f"| {i} | {c} | {display_names.get(i, '')} |")

        summary = f"Palette set: {len(final_colors)} colors.\n" + "\n".join(table_lines)
        # A3: warn, never block. Going off-palette is legitimate (a one-off VFX
        # sprite, an imported reference), but silent divergence is how a library
        # drifts out of style one sprite at a time.
        bible = session.active_style()
        # Compare the DRAWABLE colours, not the raw lists. Entry 0 never renders,
        # and a project reserves it explicitly while a preset passed straight to
        # this tool does not -- comparing whole lists made set_palette("pico8")
        # warn inside a pico8 project, which is the always-fires failure mode
        # that teaches the model to ignore the warning.
        got = set(normalize_hex(final_colors[1:]))
        want = set(normalize_hex(bible.palette[1:])) if bible is not None else set()
        if bible is not None and got != want:
            off = sorted(got - want)
            lost = sorted(want - got)
            parts = []
            if off:
                parts.append(f"{len(off)} colors are not in the project: {off[:8]}")
            if lost:
                # The common case, and the confusing one: entry 0 never renders,
                # so a preset written straight to the sprite drops whatever sat
                # there -- usually its darkest color.
                parts.append(
                    f"{len(lost)} project colors are missing or sat at index 0 "
                    f"(which never draws): {lost[:8]}"
                )
            summary = (
                f"WARNING: this sprite's palette differs from active project "
                f"{bible.project!r} — " + "; ".join(parts) + ".\n"
                "If that is deliberate, carry on. Otherwise omit `palette` so the "
                "project's own is used, which reserves index 0 for you.\n\n" + summary
            )
        blocks: list[str | MCPImage] = [summary, preview_image(render_palette_swatch(final_colors))]
        if preview:
            png, meta = render_preview(bridge, session.config.previews, path)
            w, h = meta["native"]
            blocks.append(f"sprite preview: {w}x{h} at {meta['scale']}x")
            blocks.append(preview_image(png))
        return blocks

    @mcp.tool(structured_output=False)
    def get_ramp(
        base_color: str,
        steps: int = 5,
        hue_shift: float = 15.0,
    ) -> list[str | MCPImage]:
        """Generate a shading ramp from a base color. Shadows shift hue toward
        blue/purple and highlights toward yellow, with saturation compressed
        at both ends — the standard pixel-art technique that makes shading
        read as intentional instead of a brightness slider. Does not touch
        any sprite; combine with set_palette or draw_grid's legend yourself.
        """
        if steps < 2:
            raise ToolError(code="too_few_steps", message="steps must be at least 2.")
        hex_to_rgba(base_color)  # validate before computing
        ramp = _shift_ramp(base_color, steps, hue_shift)
        summary = f"Ramp from {base_color}: " + ", ".join(ramp)
        return [summary, preview_image(render_palette_swatch(ramp))]
