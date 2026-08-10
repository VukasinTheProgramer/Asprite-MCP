from typing import Annotated, Literal, TypedDict

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from ..deps import Bridge, Session
from ..errors import ToolError
from ..render import emit
from ..state import SpriteHandle
from ..validation import lua_str, safe_path

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
        width: Annotated[int, Field(ge=1, le=1024)],
        height: Annotated[int, Field(ge=1, le=1024)],
        color_mode: Literal["indexed", "rgb", "grayscale"] = "indexed",
        overwrite: bool = False,
        preview: bool = True,
    ) -> list[str | MCPImage]:
        """Create a new sprite and make it active.

        For character sprites, 16x16 / 32x32 / 48x48 are typical. Prefer indexed
        color mode — it produces much better pixel art than free RGB.

        Example: create_sprite(name="knight", width=32, height=32, color_mode="indexed")
        """
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

        result = bridge.execute(
            f"local spr = Sprite({width}, {height}, {_COLOR_MODE_LUA[color_mode]})\n"
            f"spr.filename = {lua_str(str(path))}\n"
            f"spr:saveAs({lua_str(str(path))})\n"
            "return { path = spr.filename, width = spr.width, height = spr.height }"
        )

        session.sprites[result["path"]] = _handle(result, color_mode)
        session.active = result["path"]

        summary = f"Created {name}.aseprite — {width}x{height} {color_mode}."
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
