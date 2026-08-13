"""The `conform_image` and `detect_grid` tools — B5 surface over conform.py
and grid.py. See aseprite-mcp-upgrade-plan.md B5.
"""

import io
from pathlib import Path
from typing import Annotated, Literal, TypedDict

import numpy as np
from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from PIL import Image
from pydantic import Field

from ..cleanup import OPERATIONS, run_pipeline
from ..color import extract_palette as _extract_palette
from ..conform import conform as run_conform
from ..deps import Bridge, Session
from ..errors import ToolError
from ..history import push_snapshot
from ..palettes import load_preset
from ..reference import remove_background as _remove_background
from ..render import emit
from ..validation import hex_to_rgba, lua_str, safe_path
from .drawing import _canvas_info

_MAX_DIM = 512  # comparison-image cap; matches render.py's preview philosophy


class GridDetection(TypedDict):
    cell_w: int | None
    cell_h: int | None
    offset_x: int
    offset_y: int
    confidence: float
    is_pixel_art: bool


def _resolve_image(session: Session, image_path: str, allow_external_path: bool) -> Path:
    src = (
        Path(image_path).expanduser()
        if allow_external_path
        else safe_path(image_path, session.config.workspace)
    )
    if not src.exists():
        raise ToolError(
            code="reference_not_found",
            message=f"No file at {image_path}.",
            hint="Pass allow_external_path=True to read from outside the workspace."
            if not allow_external_path
            else None,
        )
    return src


def _load_rgba01(src: Path, remove_background: bool = False, bg_tolerance: int = 24) -> np.ndarray:
    im = Image.open(src).convert("RGBA")
    if remove_background:
        im = _remove_background(im, tolerance=bg_tolerance)
    return np.asarray(im, dtype=float) / 255.0


def _resolve_palette(
    palette: str | list[str] | None,
    sprite_hex: list[str] | None,
    source_rgba: np.ndarray | None = None,
    palette_size: int = 16,
) -> list[str]:
    if isinstance(palette, list):
        colors = palette
    elif palette == "auto":
        if source_rgba is None:
            raise ToolError(
                code="no_source_for_auto_palette",
                message="palette='auto' needs the source image to extract from.",
            )
        colors = _extract_palette(
            source_rgba[..., :3], palette_size, alpha=source_rgba[..., 3]
        )
    elif isinstance(palette, str):
        colors = load_preset(palette)["colors"]
    elif sprite_hex is not None:
        colors = sprite_hex
    else:
        raise ToolError(
            code="no_palette",
            message="No palette given and no active sprite to read one from.",
            hint="Pass palette=<preset name or hex list>, or call create_sprite / "
            "set_palette first so conform_image can default to it.",
        )
    for c in colors:
        hex_to_rgba(c)  # raises invalid_hex_color with the offending value
    return colors


def _nearest_upscale_png(rgba01: np.ndarray, max_dim: int = _MAX_DIM) -> bytes:
    """rgba01 (H,W,4) float [0,1] -> upscaled PNG bytes. NEAREST only (CLAUDE.md
    #10) — this is a comparison image, the same legibility rule applies."""
    im = Image.fromarray((np.clip(rgba01, 0, 1) * 255).round().astype(np.uint8), "RGBA")
    scale = max(1, min(16, max_dim // max(im.width, im.height, 1)))
    im = im.resize((im.width * scale, im.height * scale), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _downscale_lanczos_png(rgba01: np.ndarray, max_dim: int = _MAX_DIM) -> bytes:
    """A source image can be far larger than max_dim; LANCZOS here is a plain
    view-fit resize for the comparison image, not the conform pipeline's
    structure-preserving step (that one lives in conform.py)."""
    im = Image.fromarray((np.clip(rgba01, 0, 1) * 255).round().astype(np.uint8), "RGBA")
    if max(im.width, im.height) > max_dim:
        scale = max_dim / max(im.width, im.height)
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def register(mcp: MCPServer) -> None:
    # No structured_output=False here: this returns data, not text, and carries
    # no Image. Suppressing the schema also suppresses structured_content, so
    # callers got None instead of the GridDetection (CLAUDE.md #26/#27).
    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
    def detect_grid(image_path: str, session: Session, allow_external_path: bool = False) -> GridDetection:
        """Detect whether an image sits on a real pixel grid (a clean upscale of
        pixel art) or not (a photo, a smooth render). `conform_image` calls this
        internally, but call it directly to decide a `target_size` before
        committing — a `cell_w`/`cell_h` of 8 on a 256px image means the true
        art is 32x32.
        """
        from ..grid import detect_grid as _detect

        src = _resolve_image(session, image_path, allow_external_path)
        rgb = _load_rgba01(src)[..., :3]
        info = _detect(rgb)
        return GridDetection(**info)

    @mcp.tool(structured_output=False, annotations=ToolAnnotations(destructive_hint=True))
    def conform_image(
        bridge: Bridge,
        session: Session,
        image_path: str,
        target_size: list[Annotated[int, Field(ge=1, le=1024)]],
        palette: str | list[str] | None = None,
        palette_size: Annotated[int, Field(ge=2, le=256)] = 16,
        dither: Literal["none", "bayer2x2", "bayer4x4"] = "none",
        fit: Literal["contain", "stretch"] = "contain",
        auto_cleanup: bool = True,
        aggressiveness: float = 0.5,
        remove_background: bool = False,
        sprite: str | None = None,
        import_to_sprite: bool = True,
        allow_external_path: bool = False,
        preview: bool = True,
    ) -> list[str | MCPImage]:
        """Convert an arbitrary image (a render, a photo, a rough upscale) into
        true, grid-correct, palette-locked pixel art. This is the tool for
        turning a reference into a starting sprite — prefer it over
        `import_reference` when the source isn't already close to pixel-perfect,
        since it detects and corrects a soft/upscaled grid, downscales by
        majority-color-per-cell instead of blurring, and can auto-repair the
        result (`auto_cleanup`, chains into the `cleanup` operations).

        `palette` defaults to the target sprite's current palette; pass a preset
        name (pico8, db16, sweetie16, gameboy) or explicit hex list to lock to
        something else — that palette is written onto the sprite, replacing
        whatever was there.

        `palette="auto"` derives a `palette_size`-color palette from the source
        image itself by k-means in OKLab. Prefer it when converting reference
        art whose own colors you want to keep, over letting a preset approximate
        them. Lower `palette_size` reads as more deliberately pixel-art (12-16
        is a typical single-sprite budget); higher retains more detail but
        starts to look like a photo downscale.

        `import_to_sprite=True` (default) requires an already-created sprite
        whose canvas equals `target_size` — call `create_sprite` first. Pass
        `import_to_sprite=False` to just inspect the conform result (returns a
        before/after comparison, nothing written) before committing to a sprite.

        `fit="contain"` (default) keeps the source's aspect ratio and centres the
        result, leaving the rest of the canvas transparent. Filling the canvas
        is not the goal — a portrait forced into a square comes out visibly
        stretched. Pass `fit="stretch"` only when you actually want the subject
        distorted to fill the target.

        `remove_background=True` flood-fills transparency in from the four
        corners before conforming (same technique as `import_reference`) —
        turn it on for a screenshot or render sitting on a scene/background
        rather than a clean or already-transparent subject. It only clears
        regions connected to a corner, so a busy painted background (not a
        flat or gradient one) will only be partially removed; crop tighter to
        the subject first if it still dominates the result.

        Example — a 256x256 render of a clean 8x upscale, locked to pico8:
            conform_image(image_path="render.png", target_size=[32, 32], palette="pico8")
        """
        if len(target_size) != 2:
            raise ToolError(
                code="invalid_target_size",
                message=f"target_size must be [width, height], got {target_size}.",
            )
        tw, th = target_size
        if not 0.0 <= aggressiveness <= 1.0:
            raise ToolError(
                code="aggressiveness_out_of_range",
                message=f"aggressiveness={aggressiveness} is outside 0.0-1.0.",
            )

        src = _resolve_image(session, image_path, allow_external_path)
        rgba = _load_rgba01(src, remove_background)

        sprite_hex: list[str] | None = None
        path: str | None = None
        if import_to_sprite:
            path = session.resolve_sprite(sprite)
            canvas = _canvas_info(bridge, path)
            if canvas["width"] != tw or canvas["height"] != th:
                raise ToolError(
                    code="canvas_size_mismatch",
                    message=f"Sprite is {canvas['width']}x{canvas['height']} but "
                    f"target_size is {tw}x{th}.",
                    hint="Call create_sprite with matching dimensions first, or "
                    "pass import_to_sprite=False to preview without a sprite.",
                    context={"canvas": {"width": canvas["width"], "height": canvas["height"]}},
                )
            pal_result = bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                "local pal = spr.palettes[1]\n"
                "local hex = {}\n"
                "for i = 0, #pal - 1 do\n"
                "  local c = pal:getColor(i)\n"
                '  hex[i+1] = string.format("#%02x%02x%02x", c.red, c.green, c.blue)\n'
                "end\nreturn { hex = hex }"
            )
            sprite_hex = pal_result["hex"]

        palette_hex = _resolve_palette(palette, sprite_hex, rgba, palette_size)

        idx, alpha_mask, report = run_conform(rgba, (tw, th), palette_hex, dither=dither, fit=fit)
        # Index 0 is always transparent in Aseprite regardless of its color
        # (drawing.py's get_region_as_grid documents the same rule) -- force it
        # everywhere conform's alpha threshold says the pixel isn't there.
        out = np.where(alpha_mask, idx, 0).astype(np.int32)

        cleanup_report: dict[str, int] = {}
        if auto_cleanup:
            out, cleanup_report = run_pipeline(out, palette_hex, list(OPERATIONS), aggressiveness)

        summary_lines = [
            f"Conformed {image_path} -> {tw}x{th}, {len(palette_hex)}-color palette.",
            f"Fitted to {report['fitted_size'][0]}x{report['fitted_size'][1]} "
            f"({'aspect preserved' if fit == 'contain' else 'stretched to fill'}).",
            f"Source grid: cell={report['grid']['cell_w']}x{report['grid']['cell_h']} "
            f"confidence={report['grid']['confidence']:.2f} "
            f"({'snapped' if report['grid']['is_pixel_art'] else 'ratio downscale'}).",
        ]
        if auto_cleanup:
            summary_lines.append(
                "cleanup: " + ", ".join(f"{op}={n}px" for op, n in cleanup_report.items())
            )

        before_png = _downscale_lanczos_png(rgba)
        pal_rgba = np.array([(*hex_to_rgba(h)[:3], 255) for h in palette_hex], dtype=float) / 255.0
        after_rgba = pal_rgba[out]
        # Alpha must come from the post-cleanup indices, not conform's original
        # mask: cleanup moves pixels to index 0, which Aseprite renders as
        # transparent regardless of what color entry 0 holds. Reusing the stale
        # mask paints those pixels as palette[0] at full opacity, so the preview
        # shows speckle that isn't in the sprite that actually got written.
        after_rgba[..., 3] = (out != 0).astype(float)
        after_png = _nearest_upscale_png(after_rgba)

        summary_text = "\n".join(summary_lines)
        blocks: list[str | MCPImage] = [
            summary_text,
            "before:",
            MCPImage(data=before_png, format="png"),
            "after:",
            MCPImage(data=after_png, format="png"),
        ]

        if not import_to_sprite:
            return blocks

        assert path is not None  # import_to_sprite branch always sets it
        px_lua = ",".join(f"{x},{y},{int(out[y, x])}" for y in range(th) for x in range(tw))
        # An explicit palette (preset/list) and an auto-extracted one both have
        # to be written onto the sprite; only the "default to whatever the
        # sprite already has" case can skip it.
        if palette is not None:
            color_lua = ",".join(
                f"Color{{r={r},g={g},b={b},a={a}}}" for r, g, b, a in (hex_to_rgba(c) for c in palette_hex)
            )
            set_palette_lua = (
                f"local newpal = Palette({len(palette_hex)})\n"
                f"local __c = {{{color_lua}}}\n"
                "for i, c in ipairs(__c) do newpal:setColor(i - 1, c) end\n"
                "spr:setPalette(newpal)\n"
            )
        else:
            set_palette_lua = ""

        push_snapshot(session, path)
        bridge.execute(
            f"local spr = J.sprite({lua_str(path)})\n"
            + set_palette_lua
            + "local __layer = spr.layers[1]\n"
            "local __cel = __layer:cel(1)\n"
            "if not __cel then __cel = spr:newCel(__layer, 1) end\n"
            "J.tx(function()\n"
            "  local img = __cel.image:clone()\n"
            f"  local px = {{{px_lua}}}\n"
            "  for k = 1, #px, 3 do img:drawPixel(px[k], px[k+1], px[k+2]) end\n"
            "  __cel.image = img\n"
            "end)\n"
            "J.save(spr)\nreturn { ok = true }"
        )

        blocks[0] = summary_text + "\nWritten to sprite."
        if preview:
            blocks.extend(emit(bridge, session.config.previews, path, "", True)[1:])
        return blocks
