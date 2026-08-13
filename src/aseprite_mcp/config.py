import os
from dataclasses import dataclass
from pathlib import Path

from .discovery import find_aseprite


@dataclass
class Config:
    aseprite_exe: Path
    workspace: Path
    runtime: Path
    previews: Path
    logs: Path
    styles: Path


def load_config() -> Config:
    # ASEPRITE_MCP_WORKSPACE overrides only the art directory (§12.2 example: "~/pixel-art").
    # runtime/previews/logs are internal plumbing and always live under the fixed root.
    root = Path("~/.aseprite-mcp").expanduser()
    workspace = Path(os.environ.get("ASEPRITE_MCP_WORKSPACE", root / "workspace")).expanduser()
    runtime = root / "runtime"
    previews = root / "previews"
    logs = root / "logs"
    # Style bibles are authored content, not plumbing — same class of thing as
    # the workspace, so they get the same override. Tests depend on it too:
    # without one, every run writes projects into the developer's real home.
    styles = Path(os.environ.get("ASEPRITE_MCP_STYLES", root / "styles")).expanduser()
    for d in (workspace, runtime, previews, logs, styles):
        d.mkdir(parents=True, exist_ok=True)
    return Config(
        aseprite_exe=find_aseprite(),
        workspace=workspace,
        runtime=runtime,
        previews=previews,
        logs=logs,
        styles=styles,
    )
