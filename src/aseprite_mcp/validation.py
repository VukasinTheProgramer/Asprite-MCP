import re
from pathlib import Path

from .errors import ToolError

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def lua_str(s: str) -> str:
    """Escape a string for embedding in a generated Lua chunk. Never f-string
    a raw value into Lua — this is the only safe path (CLAUDE.md #6)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def hex_to_rgba(hex_color: str) -> tuple[int, int, int, int]:
    """Parse '#RRGGBB' or '#RRGGBBAA'. Aseprite's `Color("#hex")` constructor
    does NOT parse hex strings — confirmed empirically (M4 spike, 2026-08-10):
    it silently returns black. Always build colors from parsed r/g/b/a
    components via `Color{r=,g=,b=,a=}` instead."""
    m = _HEX_RE.match(hex_color)
    if not m:
        raise ToolError(
            code="invalid_hex_color",
            message=f"'{hex_color}' is not a valid hex color.",
            hint="Use #RRGGBB or #RRGGBBAA.",
        )
    s = m.group(1)
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    a = int(s[6:8], 16) if len(s) == 8 else 255
    return r, g, b, a


def safe_path(user_path: str, workspace: Path) -> Path:
    p = (workspace / user_path).resolve()
    if not p.is_relative_to(workspace.resolve()):
        raise ToolError(
            code="path_outside_workspace",
            message=f"Paths must be inside the workspace. Got: {user_path}",
            hint="Use a relative filename — the workspace directory is prepended automatically.",
            context={"workspace": str(workspace)},
        )
    return p
