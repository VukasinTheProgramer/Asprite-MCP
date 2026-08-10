from pathlib import Path

from mcp.server.mcpserver import MCPServer

from .palettes import available_presets, load_preset

_DIR = Path(__file__).parent / "resources"


def register(mcp: MCPServer) -> None:
    @mcp.resource(
        "aseprite://guide/pixel-art",
        name="pixel-art-guide",
        description="Craft rules: readable silhouettes, hue-shifted ramps, avoiding pillow shading and orphan pixels.",
        mime_type="text/markdown",
    )
    def pixel_art_guide() -> str:
        return (_DIR / "pixel_art_guide.md").read_text()

    @mcp.resource(
        "aseprite://guide/lua-api",
        name="lua-api-guide",
        description="Condensed Aseprite Lua API reference for run_lua, including gotchas found while building this server.",
        mime_type="text/markdown",
    )
    def lua_api_guide() -> str:
        return (_DIR / "lua_api_guide.md").read_text()

    @mcp.resource(
        "aseprite://palettes",
        name="bundled-palettes",
        description="All bundled palette presets with their colors and notes.",
        mime_type="text/markdown",
    )
    def palettes() -> str:
        lines = ["# Bundled palettes", ""]
        for name in available_presets():
            preset = load_preset(name)
            lines.append(f"## {preset['name']} (`{name}`)")
            lines.append("")
            lines.append(preset["notes"])
            lines.append("")
            lines.append(", ".join(preset["colors"]))
            lines.append("")
        return "\n".join(lines)

    # aseprite://sprite/current — not implemented. MCP resources in this SDK
    # (verified, M8 spike, 2026-08-10) get no dependency injection at all —
    # not Context, not Resolve(), even on a URI template whose variables
    # match the function's parameter names exactly. A "current sprite"
    # resource needs session state to know what "current" means, which a
    # resource function in this SDK has no way to reach. get_sprite_info
    # (a tool, which does support Resolve()) is the live-state equivalent.
