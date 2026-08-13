from typing import Literal, TypedDict

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..deps import Bridge, Session
from ..errors import ToolError
from ..history import push_snapshot
from ..render import emit
from ..validation import lua_str


class RegionGrid(TypedDict):
    grid: str
    x: int
    y: int
    width: int
    height: int

TRANSPARENT = "."
_MAX_PIXELS = 65_000

DEFAULT_LEGEND: dict[str, int] = {str(i): i for i in range(10)}
DEFAULT_LEGEND.update({chr(ord("a") + i): 10 + i for i in range(26)})


def _canvas_info(bridge: Bridge, path: str) -> dict:
    """width/height/palette_size only — draw_grid, draw_shape and fill all
    need canvas bounds before they touch a pixel; get_sprite_info computes
    more than that and would be wasted work here."""
    return bridge.execute(
        f"local spr = J.sprite({lua_str(path)})\n"
        "return { width = spr.width, height = spr.height, palette_size = #spr.palettes[1] }"
    )


def _resolve_layer_lua(layer: str | None) -> str:
    if layer is None:
        return "local __layer = spr.layers[1]"
    return (
        "local __layer = nil\n"
        f"for _, l in ipairs(spr.layers) do if l.name == {lua_str(layer)} then __layer = l break end end\n"
        f"if not __layer then error({lua_str('layer_not_found: ' + layer)}) end"
    )


def register(mcp: MCPServer) -> None:
    @mcp.tool(structured_output=False)
    def draw_grid(
        bridge: Bridge,
        session: Session,
        grid: str,
        x: int = 0,
        y: int = 0,
        sprite: str | None = None,
        layer: str | None = None,
        frame: int = 1,
        legend: dict[str, int] | None = None,
        preview: bool = True,
    ) -> list[str | MCPImage]:
        """Draw a rectangular block of pixels from a text grid.

        Each line is a row; each character is one pixel. Characters map to
        palette indices via `legend`, defaulting to: '.' = transparent,
        '0'-'9' = palette index 0-9, 'a'-'z' = index 10-35.

        Example (a 7x5 face using palette indices 1 and 2):
            ..111..
            .11211.
            1122211
            .11211.
            ..111..

        All rows must be the same length. This is the preferred way to draw —
        much more reliable than individual pixel calls.
        """
        path = session.resolve_sprite(sprite)
        active_legend = {**DEFAULT_LEGEND, **(legend or {})}

        # 1. strip blank leading/trailing lines; reject empty
        lines = grid.split("\n")
        while lines and lines[0].strip() == "":
            lines.pop(0)
        while lines and lines[-1].strip() == "":
            lines.pop()
        if not lines:
            raise ToolError(
                code="empty_grid",
                message="Grid has no rows after stripping blank lines.",
                hint="Pass at least one non-blank row.",
            )

        # 2. all rows equal length
        lengths = {i + 1: len(row) for i, row in enumerate(lines)}
        if len(set(lengths.values())) > 1:
            raise ToolError(
                code="grid_rows_uneven",
                message="All grid rows must be the same length.",
                hint="Pad shorter rows with '.' for transparent.",
                context={"row_lengths": lengths},
            )
        width, height = lengths[1], len(lines)

        # 3. every character is in the legend
        bad_chars = {c for row in lines for c in row if c != TRANSPARENT and c not in active_legend}
        if bad_chars:
            raise ToolError(
                code="unknown_grid_char",
                message=f"Unrecognized grid characters: {sorted(bad_chars)}",
                hint="Add them to `legend`, or use only default legend characters.",
                context={"valid_legend": sorted({TRANSPARENT, *active_legend})},
            )

        # 4-5. palette bounds + canvas bounds (one read-only bridge call, no mutation yet)
        canvas = _canvas_info(bridge, path)
        used_indices = {active_legend[c] for row in lines for c in row if c != TRANSPARENT}
        oob_indices = {i for i in used_indices if i >= canvas["palette_size"]}
        if oob_indices:
            raise ToolError(
                code="palette_index_out_of_range",
                message=f"Grid references palette index {sorted(oob_indices)} but the "
                f"palette has {canvas['palette_size']} colors (0-{canvas['palette_size'] - 1}).",
                hint="Use an in-range index, or call set_palette with more colors first.",
                context={"bad_indices": sorted(oob_indices), "palette_size": canvas["palette_size"]},
            )
        if x < 0 or y < 0 or x + width > canvas["width"] or y + height > canvas["height"]:
            raise ToolError(
                code="out_of_bounds",
                message=f"Grid at ({x},{y}) sized {width}x{height} overhangs the "
                f"{canvas['width']}x{canvas['height']} canvas.",
                hint="Move x/y, shrink the grid, or resize the canvas first.",
                context={"canvas": {"width": canvas["width"], "height": canvas["height"]}},
            )

        total_pixels = width * height
        if total_pixels > _MAX_PIXELS:
            raise ToolError(
                code="grid_too_large",
                message=f"Grid is {total_pixels} pixels; the cap per call is {_MAX_PIXELS}.",
                hint="Split the drawing across multiple draw_grid calls.",
            )

        # Build the flat pixel list. '.' pixels are simply skipped — a fresh
        # cel is already all-transparent, so "don't draw" IS "leave transparent".
        px: list[int] = []
        pixels_written = 0
        for row_i, row in enumerate(lines):
            for col_i, ch in enumerate(row):
                if ch == TRANSPARENT:
                    continue
                px.extend([x + col_i, y + row_i, active_legend[ch]])
                pixels_written += 1
        px_lua = ",".join(str(n) for n in px)

        push_snapshot(session, path)
        bridge.execute(
            f"local spr = J.sprite({lua_str(path)})\n"
            f"{_resolve_layer_lua(layer)}\n"
            f"local __frame = {frame}\n"
            "if not spr.frames[__frame] then error('frame_out_of_range: ' .. __frame) end\n"
            "local __cel = __layer:cel(__frame)\n"
            "if not __cel then __cel = spr:newCel(__layer, __frame) end\n"
            "J.tx(function()\n"
            "  local img = __cel.image:clone()\n"
            f"  local px = {{{px_lua}}}\n"
            "  for k = 1, #px, 3 do img:drawPixel(px[k], px[k+1], px[k+2]) end\n"
            "  __cel.image = img\n"
            "end)\n"
            "J.save(spr)\n"
            "return { ok = true }"
        )

        summary = f"Drew {pixels_written} pixels ({width}x{height} at {x},{y})."
        return emit(bridge, session.config.previews, path, summary, preview)

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
    def get_region_as_grid(
        bridge: Bridge,
        session: Session,
        x: int = 0,
        y: int = 0,
        width: int | None = None,
        height: int | None = None,
        sprite: str | None = None,
        layer: str | None = None,
        frame: int = 1,
        legend: dict[str, int] | None = None,
    ) -> RegionGrid:
        """Read a canvas region back as a palette-index text grid — the same
        format `draw_grid` accepts. Use this to inspect and edit existing
        pixels. Omit width/height to read the whole canvas.
        """
        path = session.resolve_sprite(sprite)
        canvas = _canvas_info(bridge, path)
        w = width if width is not None else canvas["width"] - x
        h = height if height is not None else canvas["height"] - y
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > canvas["width"] or y + h > canvas["height"]:
            raise ToolError(
                code="out_of_bounds",
                message=f"Region ({x},{y}) sized {w}x{h} is outside the "
                f"{canvas['width']}x{canvas['height']} canvas.",
                context={"canvas": {"width": canvas["width"], "height": canvas["height"]}},
            )

        active_legend = {**DEFAULT_LEGEND, **(legend or {})}
        index_to_char = {}
        for char, idx in active_legend.items():
            index_to_char[idx] = char  # later entries win on collision
        # Index 0 is the sprite's transparentColor by default — Aseprite composites
        # it as alpha=0 regardless of whether it was explicitly painted or never
        # touched (confirmed empirically, M3 spike). '.' always wins here: legend
        # entries mapping a character to 0 (e.g. the default legend's '0') can't
        # be told apart from "untouched" at the pixel level, so transparent takes
        # priority rather than guessing.
        index_to_char[0] = TRANSPARENT

        result = bridge.execute(
            f"local spr = J.sprite({lua_str(path)})\n"
            f"{_resolve_layer_lua(layer)}\n"
            f"local __frame = {frame}\n"
            "if not spr.frames[__frame] then error('frame_out_of_range: ' .. __frame) end\n"
            "local __cel = __layer:cel(__frame)\n"
            "local rows = {}\n"
            f"for row = 0, {h - 1} do\n"
            "  local r = {}\n"
            f"  for col = 0, {w - 1} do\n"
            f"    r[col+1] = __cel and __cel.image:getPixel({x}+col, {y}+row) or 0\n"
            "  end\n"
            "  rows[row+1] = r\n"
            "end\n"
            "return { rows = rows }"
        )

        unmapped: set[int] = set()
        lines = []
        for row in result["rows"]:
            chars = []
            for idx in row:
                if idx in index_to_char:
                    chars.append(index_to_char[idx])
                else:
                    unmapped.add(idx)
                    chars.append("?")
            lines.append("".join(chars))

        if unmapped:
            raise ToolError(
                code="unrepresentable_pixel_index",
                message=f"Region contains palette indices with no legend character: {sorted(unmapped)}",
                hint="Pass a `legend` covering these indices — default legend only covers 0-35.",
                context={"bad_indices": sorted(unmapped)},
            )

        return {"grid": "\n".join(lines), "x": x, "y": y, "width": w, "height": h}

    @mcp.tool(structured_output=False)
    def draw_shape(
        bridge: Bridge,
        session: Session,
        shape: Literal["line", "rect", "ellipse", "polyline", "point"],
        points: list[list[int]],
        color: int,
        filled: bool = False,
        sprite: str | None = None,
        layer: str | None = None,
        frame: int = 1,
        mirror: Literal["none", "horizontal", "vertical", "both"] = "none",
        preview: bool = True,
    ) -> list[str | MCPImage]:
        """Draw a geometric primitive. `color` is a palette index.

        points: [[x,y], ...] — exactly 2 for line/rect/ellipse, exactly 1 for
        point, 2+ for polyline. Use `mirror` for symmetric characters — drawing
        half a face and mirroring is far more reliable than drawing both halves.
        """
        path = session.resolve_sprite(sprite)

        n = len(points)
        required = {"line": 2, "rect": 2, "ellipse": 2, "point": 1}
        if shape in required and n != required[shape]:
            raise ToolError(
                code="wrong_point_count",
                message=f"shape='{shape}' needs exactly {required[shape]} point(s), got {n}.",
                hint="See the tool docstring for point counts per shape.",
            )
        if shape == "polyline" and n < 2:
            raise ToolError(
                code="wrong_point_count",
                message=f"shape='polyline' needs at least 2 points, got {n}.",
            )

        canvas = _canvas_info(bridge, path)
        if color < 0 or color >= canvas["palette_size"]:
            raise ToolError(
                code="palette_index_out_of_range",
                message=f"color={color} but the palette has {canvas['palette_size']} colors "
                f"(0-{canvas['palette_size'] - 1}).",
                hint="Use an in-range index, or call set_palette with more colors first.",
                context={"palette_size": canvas["palette_size"]},
            )

        w, h = canvas["width"], canvas["height"]

        def mirrored(pts: list[list[int]]) -> list[list[int]]:
            out = []
            for px, py in pts:
                mx = w - 1 - px if mirror in ("horizontal", "both") else px
                my = h - 1 - py if mirror in ("vertical", "both") else py
                out.append([mx, my])
            return out

        point_sets = [points]
        if mirror != "none":
            point_sets.append(mirrored(points))

        if shape == "point":
            # A single pixel — plain drawPixel is simpler and carries none of
            # useTool's cel-shrink risk (see J.normalize_cel), so skip useTool.
            px_lua = ",".join(
                f"{px},{py},{color}" for pts in point_sets for px, py in pts
            )
            lua_body = (
                "J.tx(function()\n"
                "  local img = __cel.image:clone()\n"
                f"  local px = {{{px_lua}}}\n"
                "  for k = 1, #px, 3 do img:drawPixel(px[k], px[k+1], px[k+2]) end\n"
                "  __cel.image = img\n"
                "end)\n"
            )
        else:
            tool_name = {
                "line": "line",
                "polyline": "line",
                "rect": "filled_rectangle" if filled else "rectangle",
                "ellipse": "filled_ellipse" if filled else "ellipse",
            }[shape]
            calls = []
            for pts in point_sets:
                pts_lua = ", ".join(f"Point({px},{py})" for px, py in pts)
                calls.append(
                    "app.useTool{\n"
                    f'  tool = {lua_str(tool_name)}, color = Color{{ index = {color} }},\n'
                    f"  points = {{ {pts_lua} }},\n"
                    "  layer = __layer, frame = __frame,\n"
                    "}"
                )
            lua_body = "\n".join(calls) + "\nJ.normalize_cel(spr, __cel)\n"

        push_snapshot(session, path)
        bridge.execute(
            f"local spr = J.sprite({lua_str(path)})\n"
            f"{_resolve_layer_lua(layer)}\n"
            f"local __frame = {frame}\n"
            "if not spr.frames[__frame] then error('frame_out_of_range: ' .. __frame) end\n"
            "local __cel = __layer:cel(__frame)\n"
            "if not __cel then __cel = spr:newCel(__layer, __frame) end\n"
            "J.tx(function()\n"
            f"{lua_body}\n"
            "end)\n"
            "J.save(spr)\n"
            "return { ok = true }"
        )

        n_drawn = len(point_sets)
        note = f" (mirrored {mirror})" if mirror != "none" else ""
        summary = f"Drew {shape}{note}: {n_drawn} stroke(s)."
        return emit(bridge, session.config.previews, path, summary, preview)

    @mcp.tool(structured_output=False)
    def fill(
        bridge: Bridge,
        session: Session,
        x: int,
        y: int,
        color: int,
        tolerance: int = 0,
        contiguous: bool = True,
        sprite: str | None = None,
        layer: str | None = None,
        frame: int = 1,
        preview: bool = True,
    ) -> list[str | MCPImage]:
        """Flood-fill starting at (x,y) with a palette index.

        `contiguous=False` fills every matching pixel in the image, not just
        the connected region touching (x,y).
        """
        path = session.resolve_sprite(sprite)
        canvas = _canvas_info(bridge, path)
        if color < 0 or color >= canvas["palette_size"]:
            raise ToolError(
                code="palette_index_out_of_range",
                message=f"color={color} but the palette has {canvas['palette_size']} colors "
                f"(0-{canvas['palette_size'] - 1}).",
                hint="Use an in-range index, or call set_palette with more colors first.",
                context={"palette_size": canvas["palette_size"]},
            )
        if x < 0 or y < 0 or x >= canvas["width"] or y >= canvas["height"]:
            raise ToolError(
                code="out_of_bounds",
                message=f"({x},{y}) is outside the {canvas['width']}x{canvas['height']} canvas.",
            )

        push_snapshot(session, path)
        bridge.execute(
            f"local spr = J.sprite({lua_str(path)})\n"
            f"{_resolve_layer_lua(layer)}\n"
            f"local __frame = {frame}\n"
            "if not spr.frames[__frame] then error('frame_out_of_range: ' .. __frame) end\n"
            "local __cel = __layer:cel(__frame)\n"
            "if not __cel then __cel = spr:newCel(__layer, __frame) end\n"
            "J.tx(function()\n"
            "app.useTool{\n"
            f'  tool = "paint_bucket", color = Color{{ index = {color} }},\n'
            f"  points = {{ Point({x},{y}) }},\n"
            f"  tolerance = {tolerance}, contiguous = {'true' if contiguous else 'false'},\n"
            "  layer = __layer, frame = __frame,\n"
            "}\n"
            "end)\n"
            "J.normalize_cel(spr, __cel)\n"
            "J.save(spr)\n"
            "return { ok = true }"
        )

        summary = f"Filled from ({x},{y})."
        return emit(bridge, session.config.previews, path, summary, preview)
