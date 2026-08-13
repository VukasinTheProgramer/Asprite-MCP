"""The `cleanup` tool — deterministic pixel-art repair on an existing sprite.
Pixel logic lives in ../cleanup.py (pure numpy, unit-tested); this module only
moves pixels across the bridge. See aseprite-mcp-upgrade-plan.md B2.5.
"""

import numpy as np
from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..cleanup import OPERATIONS, run_pipeline
from ..deps import Bridge, Session
from ..errors import ToolError
from ..history import push_snapshot
from ..render import emit
from ..validation import lua_str
from .drawing import _resolve_layer_lua


def register(mcp: MCPServer) -> None:
    @mcp.tool(structured_output=False, annotations=ToolAnnotations(destructive_hint=True))
    def cleanup(
        bridge: Bridge,
        session: Session,
        sprite: str | None = None,
        operations: list[str] | None = None,
        aggressiveness: float = 0.5,
        layer: str | None = None,
        frame: int = 1,
        preview: bool = True,
    ) -> list[str | MCPImage]:
        """Repair pixel-art artifacts on a sprite: anti-aliasing fringes, orphan
        single pixels, and irregular diagonal staircases. Deterministic — no
        model judgement involved, so prefer it over hand-editing pixels when the
        problem is one of these three.

        Reach for this after `import_reference`, after any downscale, or on
        art that came from outside Aseprite. It is a no-op on already-clean
        hand-drawn art, so running it costs nothing but a call.

        `operations` defaults to all of them. `aggressiveness` (0-1) is the one
        knob: 0.5 is a safe default, raise it if artifacts survive, lower it if
        real detail is being eaten. The tool reports how many pixels each
        operation changed — a large `remove_aa` count on hand-drawn art means
        it is too aggressive.

        Example — clean up a freshly imported reference, gently:
            cleanup(operations=["remove_aa", "remove_orphans"], aggressiveness=0.3)
        """
        ops = list(operations) if operations is not None else list(OPERATIONS)
        unknown = [o for o in ops if o not in OPERATIONS]
        if unknown:
            raise ToolError(
                code="unknown_cleanup_operation",
                message=f"Unknown cleanup operation(s): {unknown}.",
                hint=f"Valid operations are {list(OPERATIONS)}; omit the argument to run all.",
                context={"valid": list(OPERATIONS)},
            )
        if not 0.0 <= aggressiveness <= 1.0:
            raise ToolError(
                code="aggressiveness_out_of_range",
                message=f"aggressiveness={aggressiveness} is outside 0.0-1.0.",
                hint="Use 0.5 unless you have a reason; higher repairs more, eats more detail.",
            )

        path = session.resolve_sprite(sprite)
        read = bridge.execute(
            f"local spr = J.sprite({lua_str(path)})\n"
            f"{_resolve_layer_lua(layer)}\n"
            f"local __frame = {frame}\n"
            "if not spr.frames[__frame] then error('frame_out_of_range: ' .. __frame) end\n"
            "local __cel = __layer:cel(__frame)\n"
            "local rows = {}\n"
            "for row = 0, spr.height - 1 do\n"
            "  local r = {}\n"
            "  for col = 0, spr.width - 1 do\n"
            "    r[col+1] = __cel and __cel.image:getPixel(col, row) or 0\n"
            "  end\n"
            "  rows[row+1] = r\n"
            "end\n"
            "local pal, hex = spr.palettes[1], {}\n"
            "for i = 0, #pal - 1 do\n"
            "  local c = pal:getColor(i)\n"
            '  hex[i+1] = string.format("#%02x%02x%02x", c.red, c.green, c.blue)\n'
            "end\n"
            "return { rows = rows, hex = hex }"
        )
        idx = np.array(read["rows"], dtype=np.int32)
        out, report = run_pipeline(idx, read["hex"], ops, aggressiveness)

        changed = np.argwhere(out != idx)
        summary_lines = [f"{op}: {n} pixels" for op, n in report.items()]
        if changed.size == 0:
            return emit(
                bridge,
                session.config.previews,
                path,
                "Already clean — nothing changed.\n" + "\n".join(summary_lines),
                preview,
            )

        px_lua = ",".join(f"{int(x)},{int(y)},{int(out[y, x])}" for y, x in changed)
        push_snapshot(session, path)
        bridge.execute(
            f"local spr = J.sprite({lua_str(path)})\n"
            f"{_resolve_layer_lua(layer)}\n"
            f"local __cel = __layer:cel({frame})\n"
            f"if not __cel then __cel = spr:newCel(__layer, {frame}) end\n"
            "J.tx(function()\n"
            "  local img = __cel.image:clone()\n"
            f"  local px = {{{px_lua}}}\n"
            "  for k = 1, #px, 3 do img:drawPixel(px[k], px[k+1], px[k+2]) end\n"
            "  __cel.image = img\n"
            "end)\n"
            "J.save(spr)\n"
            "return { ok = true }"
        )

        summary = f"Cleaned {len(changed)} pixels.\n" + "\n".join(summary_lines)
        return emit(bridge, session.config.previews, path, summary, preview)
