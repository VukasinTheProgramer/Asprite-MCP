from typing import Annotated, Literal, TypedDict

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from ..deps import Bridge, Session
from ..errors import ToolError
from ..history import push_snapshot
from ..render import emit
from ..state import SpriteHandle
from ..validation import hex_to_rgba, lua_str, safe_path

_COLOR_MODE_LUA_TO_STR = """
local __cm = "rgb"
if spr.colorMode == ColorMode.INDEXED then __cm = "indexed"
elseif spr.colorMode == ColorMode.GRAYSCALE then __cm = "grayscale" end
""".strip()


class LayerInfo(TypedDict):
    name: str
    visible: bool
    opacity: int


class FrameInfo(TypedDict):
    duration: float


class SpriteInfo(TypedDict):
    path: str
    width: int
    height: int
    color_mode: str
    layers: list[LayerInfo]
    frames: list[FrameInfo]
    palette_size: int
    tag_count: int

_COLOR_MODE_LUA = {
    "indexed": "ColorMode.INDEXED",
    "rgb": "ColorMode.RGB",
    "grayscale": "ColorMode.GRAYSCALE",
}

_WARN_DIM = 128


def register(mcp: MCPServer) -> None:
    @mcp.tool(structured_output=False)
    def create_sprite(
        bridge: Bridge,
        session: Session,
        name: str,
        width: Annotated[int, Field(ge=1, le=1024)] | None = None,
        height: Annotated[int, Field(ge=1, le=1024)] | None = None,
        asset_type: Literal["character", "item", "tile", "portrait", "vfx"] | None = None,
        color_mode: Literal["indexed", "rgb", "grayscale"] = "indexed",
        overwrite: bool = False,
        preview: bool = True,
    ) -> list[str | MCPImage]:
        """Create a new sprite and make it active.

        With a style project active (see the `style` tool), pass `asset_type`
        instead of dimensions — the project's canvas default and palette are
        applied for you, which is what keeps a library visually consistent.
        Without one, pass `width` and `height` explicitly.

        For character sprites, 16x16 / 32x32 / 48x48 are typical. Prefer indexed
        color mode — it produces much better pixel art than free RGB.

        Example: create_sprite(name="knight", asset_type="character")
        Example: create_sprite(name="knight", width=32, height=32)
        """
        bible = session.active_style()
        if width is None or height is None:
            if bible is None or asset_type is None:
                raise ToolError(
                    code="sprite_size_unresolved",
                    message="Sprite size was not given and could not be derived.",
                    hint=(
                        "Pass width and height, or pass asset_type with a style project "
                        "active (style(action='set_active', project=...))."
                    ),
                    context={"active_project": session.active_project},
                )
            if asset_type not in bible.canvas_defaults:
                raise ToolError(
                    code="asset_type_not_in_style",
                    message=f"Project {bible.project!r} has no canvas default for {asset_type!r}.",
                    hint=f"Defined types: {sorted(bible.canvas_defaults)}. Or pass width/height.",
                    context={"defined": sorted(bible.canvas_defaults)},
                )
            width, height = bible.canvas_defaults[asset_type]
        # BatchBridge.execute is a blocking call. The tool is a plain `def`
        # (not async) so the SDK runs it on a worker thread instead of stalling
        # the event loop (CLAUDE.md #29) — no await, no thread-pool wrapping needed.
        path = safe_path(f"{name}.aseprite", session.config.workspace)
        if path.exists() and not overwrite:
            raise ToolError(
                code="sprite_exists",
                message=f"{name}.aseprite already exists in the workspace.",
                hint="Pass overwrite=True to replace it, or pick a different name.",
                context={"path": str(path)},
            )

        push_snapshot(session, str(path))  # no-op if this is a fresh name (nothing to snapshot yet)
        result = bridge.execute(
            f"local spr = Sprite({width}, {height}, {_COLOR_MODE_LUA[color_mode]})\n"
            f"spr.filename = {lua_str(str(path))}\n"
            f"spr:saveAs({lua_str(str(path))})\n"
            "return { path = spr.filename, width = spr.width, height = spr.height }"
        )

        session.sprites[result["path"]] = _handle(result, color_mode)
        session.active = result["path"]

        summary = f"Created {name}.aseprite — {width}x{height} {color_mode}."
        if bible is not None:
            set_pal = ",".join(
                "Color{{r={},g={},b={},a={}}}".format(*hex_to_rgba(c)) for c in bible.palette
            )
            bridge.execute(
                f"local spr = J.sprite({lua_str(result['path'])})\n"
                "J.tx(function()\n"
                f"  local cols = {{{set_pal}}}\n"
                "  local pal = Palette(#cols)\n"
                "  for i = 1, #cols do pal:setColor(i - 1, cols[i]) end\n"
                "  spr:setPalette(pal)\n"
                "end)\n"
                "J.save(spr)\n"
                "return { ok = true }"
            )
            session.sprites[result["path"]].palette = list(bible.palette)
            summary += f" On {bible.project!r} palette ({len(bible.palette)} colors)."
        if width > _WARN_DIM or height > _WARN_DIM:
            summary += f" Note: above {_WARN_DIM}x{_WARN_DIM}, pixel art gets hard to control."
        return emit(bridge, session.config.previews, result["path"], summary, preview)

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
    def get_sprite_info(
        bridge: Bridge,
        session: Session,
        sprite: str | None = None,
    ) -> SpriteInfo:
        """Inspect a sprite: dimensions, color mode, layers, frames, palette size.

        Cheap and read-only — the way to re-orient after context loss. Omit
        `sprite` to inspect the currently active one.
        """
        path = session.resolve_sprite(sprite)
        return bridge.execute(
            f"local spr = J.sprite({lua_str(path)})\n"
            "local layer_info = {}\n"
            "for i, l in ipairs(spr.layers) do\n"
            "  layer_info[i] = { name = l.name, visible = l.isVisible, opacity = l.opacity or 255 }\n"
            "end\n"
            "local frame_info = {}\n"
            "for i, f in ipairs(spr.frames) do\n"
            "  frame_info[i] = { duration = f.duration }\n"
            "end\n"
            f"{_COLOR_MODE_LUA_TO_STR}\n"
            "return {\n"
            "  path = spr.filename, width = spr.width, height = spr.height, color_mode = __cm,\n"
            "  layers = layer_info, frames = frame_info,\n"
            "  palette_size = #spr.palettes[1], tag_count = #spr.tags,\n"
            "}"
        )


def _handle(result: dict, color_mode: str) -> SpriteHandle:
    return SpriteHandle(
        path=result["path"],
        width=result["width"],
        height=result["height"],
        color_mode=color_mode,
    )
