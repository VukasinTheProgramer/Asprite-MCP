from pathlib import Path
from typing import Literal

import numpy as np

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer

from .. import reference as pipeline
from ..deps import Bridge, Session
from ..cleanup import OPERATIONS, run_pipeline
from ..color import TRANSPARENT_INDEX
from ..conform import conform as run_conform
from ..errors import ToolError
from ..history import push_snapshot
from ..render import emit
from ..validation import lua_str, safe_path
from .drawing import DEFAULT_LEGEND, TRANSPARENT, _canvas_info


def register(mcp: MCPServer) -> None:
    @mcp.tool(structured_output=False)
    def import_reference(
        bridge: Bridge,
        session: Session,
        image_path: str,
        sprite: str | None = None,
        target_width: int | None = None,
        target_height: int | None = None,
        mode: Literal["trace", "quantize", "both"] = "both",
        remove_background: bool = True,
        dither: Literal["none", "bayer2x2", "bayer4x4"] = "none",
        opacity: int = 128,
        allow_external_path: bool = False,
        preview: bool = True,
    ) -> list[str | MCPImage]:
        """Import a reference as a pair of LAYERS to work against: 'reference'
        (locked, semi-transparent, look-but-don't-touch) and
        'reference_quantized' (an editable starting point).

        **Prefer `conform_image` for turning a reference into a sprite.** It
        writes conformed pixels straight into the cel and is the better tool
        whenever you want the reference to *become* the sprite. Reach for this
        one only when you specifically want the locked baseline layer to keep
        comparing against while you redraw — that layer pair is the only thing
        it does that `conform_image` does not.

        Both layers hold the same quantized pixels: indexed sprites cannot
        represent true colour, so "trace over the true-colour original"
        collapses to this for our indexed-first default. Still useful as a
        locked baseline versus an editable copy.

        Pixels go through the same conform + cleanup pipeline `conform_image`
        uses, so the two tools no longer disagree about what a downscale of the
        same image looks like.

        `image_path` must be inside the workspace unless
        `allow_external_path=True` (CLAUDE.md #20 — reading outside the
        workspace requires an explicit opt-in).
        """
        # One span across the whole read-modify-write: the pixels written
        # below are computed from the read above, so another tool landing
        # between them would be clobbered by our stale result (CLAUDE.md #2).
        with bridge.serialized():
            path = session.resolve_sprite(sprite)

            if allow_external_path:
                src = Path(image_path).expanduser()
            else:
                src = safe_path(image_path, session.config.workspace)
            if not src.exists():
                raise ToolError(
                    code="reference_not_found",
                    message=f"No file at {image_path}.",
                    hint="Pass allow_external_path=True to read from outside the workspace."
                    if not allow_external_path
                    else None,
                )

            canvas = _canvas_info(bridge, path)
            w = target_width or canvas["width"]
            h = target_height or canvas["height"]

            pal_result = bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                "local pal = spr.palettes[1]\n"
                "local hex = {}\n"
                "for i = 0, #pal - 1 do\n"
                "  local c = pal:getColor(i)\n"
                '  hex[i+1] = string.format("#%02x%02x%02x", c.red, c.green, c.blue)\n'
                "end\nreturn { hex = hex }"
            )
            palette_hex: list[str] = pal_result["hex"]

            # Routed through conform.py rather than this module's own older
            # downscale+quantize (upgrade-plan repo diff: "reference.py MOD route
            # through conform.py"). The separate path had drifted badly: no grid
            # detection, no modal downscale, no aspect fit, and -- because it never
            # went through _resolve_palette -- it still reserved nothing at palette
            # entry 0, so importing against pico8 erased 30% of a sprite's opaque
            # pixels exactly as conform_image used to.
            im = pipeline.load_and_orient(str(src))
            if remove_background:
                im = pipeline.remove_background(im)
            rgba = np.asarray(im.convert("RGBA"), dtype=float) / 255.0
            # No reserve_transparent() here, unlike conform_image: this reads the
            # sprite's OWN palette, whose entry 0 is already the transparent slot by
            # convention, and this tool does not rewrite the palette. Prepending a
            # second placeholder would shift every index onto a palette the sprite
            # does not have. conform's reserve_index_0 keeps quantization off entry 0,
            # which is the part that was actually missing.
            indices, alpha_mask, _ = run_conform(rgba, (w, h), palette_hex, dither=dither)
            indices = np.where(alpha_mask, indices, TRANSPARENT_INDEX).astype(np.int32)
            indices, _ = run_pipeline(indices, palette_hex, list(OPERATIONS), 0.5)

            px = []
            for row in range(h):
                for col in range(w):
                    px.extend([col, row, int(indices[row, col])])
            px_lua = ",".join(str(n) for n in px)

            layer_lua_parts = []
            if mode in ("trace", "both"):
                layer_lua_parts.append(
                    "local ref = spr:newLayer()\n"
                    'ref.name = "reference"\n'
                    f"ref.opacity = {opacity}\n"
                    f"local refcel = spr:newCel(ref, spr.frames[1])\n"
                    "local refimg = refcel.image:clone()\n"
                    f"local px = {{{px_lua}}}\n"
                    "for k = 1, #px, 3 do refimg:drawPixel(px[k], px[k+1], px[k+2]) end\n"
                    "refcel.image = refimg\n"
                    "ref.isEditable = false\n"
                )
            if mode in ("quantize", "both"):
                layer_lua_parts.append(
                    "local rq = spr:newLayer()\n"
                    'rq.name = "reference_quantized"\n'
                    "local rqcel = spr:newCel(rq, spr.frames[1])\n"
                    "local rqimg = rqcel.image:clone()\n"
                    f"local px2 = {{{px_lua}}}\n"
                    "for k = 1, #px2, 3 do rqimg:drawPixel(px2[k], px2[k+1], px2[k+2]) end\n"
                    "rqcel.image = rqimg\n"
                )
            push_snapshot(session, path)
            bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                + "J.tx(function()\n"
                + "\n".join(layer_lua_parts)
                + "\nend)\n"
                + "J.save(spr)\nreturn { ok = true }"
            )

            active_legend = {**DEFAULT_LEGEND}
            index_to_char = {}
            for char, idx in active_legend.items():
                index_to_char[idx] = char
            index_to_char[0] = TRANSPARENT
            grid_lines = []
            for row in range(h):
                grid_lines.append("".join(index_to_char.get(int(indices[row, c]), "?") for c in range(w)))
            grid_text = "\n".join(grid_lines)

            summary = f"Imported reference ({w}x{h}, mode={mode}) as {'2 layers' if mode == 'both' else '1 layer'}.\n{grid_text}"
            return emit(bridge, session.config.previews, path, summary, preview)
