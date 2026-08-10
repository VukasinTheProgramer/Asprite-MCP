from pathlib import Path

from .errors import ToolError


def lua_str(s: str) -> str:
    """Escape a string for embedding in a generated Lua chunk. Never f-string
    a raw value into Lua — this is the only safe path (CLAUDE.md #6)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


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
