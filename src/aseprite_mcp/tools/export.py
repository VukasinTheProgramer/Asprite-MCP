import io
import subprocess
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer
from PIL import Image

from ..deps import Bridge, Session
from ..errors import ToolError
from ..render import preview_image
from ..validation import lua_str, safe_path

_EXT = {"png": ".png", "gif": ".gif", "spritesheet": ".png"}


def _upscale_preview(png_path: Path, max_dim: int = 512) -> bytes:
    im = Image.open(png_path).convert("RGBA")
    scale = max(1, min(16, max_dim // max(im.width, im.height)))
    im = im.resize((im.width * scale, im.height * scale), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def register(mcp: MCPServer) -> None:
    @mcp.tool(structured_output=False)
    def export(
        bridge: Bridge,
        session: Session,
        format: Literal["png", "gif", "spritesheet"],
        sprite: str | None = None,
        path: str | None = None,
        frame: int = 1,
        scale: int = 1,
        sheet_type: Literal["horizontal", "vertical", "rows", "columns", "packed"] = "horizontal",
        include_json: bool = True,
        trim: bool = False,
        padding: int = 0,
        preview: bool = True,
    ) -> list[str | MCPImage]:
        """Export a sprite. Goes through Aseprite's CLI export flags directly
        (--save-as / --sheet), not the Lua bridge — better tested than the
        scripted equivalent (§5.7).

        format='png' exports a single frame (`frame`, default 1) — PNG can't
        hold an animation. format='gif' exports the full animation.
        format='spritesheet' lays out every frame per `sheet_type`, plus a
        JSON metadata file unless include_json=False. `padding` maps to
        Aseprite's --shape-padding (space between frames).

        Omit `path` for a default name derived from the sprite.
        """
        sprite_path = session.resolve_sprite(sprite)
        stem = sprite_path.rsplit("/", 1)[-1].removesuffix(".aseprite")

        if path is None:
            suffix = "-sheet" if format == "spritesheet" else ""
            out_path = session.config.workspace / f"{stem}{suffix}{_EXT[format]}"
        else:
            out_path = safe_path(path, session.config.workspace)

        cmd = [str(session.config.aseprite_exe), "--batch", sprite_path]
        if scale != 1:
            cmd += ["--scale", str(scale)]
        if trim:
            cmd += ["--trim"]

        json_path = out_path.with_suffix(".json")
        if format == "png":
            # Aseprite silently clamps an out-of-range --frame-range to
            # whatever frames actually exist rather than erroring — verified
            # empirically (M7 spike, 2026-08-10): requesting frame 99 on a
            # 1-frame sprite exported frame 1 with exit 0, no warning.
            frame_count = bridge.execute(
                f"local spr = J.sprite({lua_str(sprite_path)})\nreturn {{ count = #spr.frames }}"
            )["count"]
            if not 1 <= frame <= frame_count:
                raise ToolError(
                    code="frame_out_of_range",
                    message=f"frame={frame} but the sprite only has {frame_count} frame(s).",
                    hint=f"Use a frame between 1 and {frame_count}.",
                )
            cmd += ["--frame-range", f"{frame},{frame}", "--save-as", str(out_path)]
        elif format == "gif":
            cmd += ["--save-as", str(out_path)]
        else:  # spritesheet
            cmd += ["--sheet", str(out_path), "--sheet-type", sheet_type]
            if padding:
                cmd += ["--shape-padding", str(padding)]
            if include_json:
                cmd += ["--data", str(json_path), "--format", "json-array"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        # Verified empirically (M7 spike, 2026-08-10): a multi-frame sprite
        # exported to a single-image PNG without --frame-range exits 0 with
        # no output and no error message — Aseprite's CLI can fail silently.
        # Never trust the exit code alone; check the file actually landed.
        if not out_path.exists():
            raise ToolError(
                code="export_failed",
                message=f"Aseprite exited {result.returncode} but {out_path.name} was not created.",
                hint="Check `frame` is in range and the sprite has at least one frame.",
                context={"stdout": result.stdout[-500:], "stderr": result.stderr[-500:]},
            )

        summary = f"Exported {format} to {out_path.name}"
        if format == "spritesheet" and include_json:
            summary += f" (+ {json_path.name})" if json_path.exists() else " (json metadata missing)"
        blocks: list[str | MCPImage] = [summary + "."]
        if preview:
            # Pillow reads a GIF's first frame by default — fine for a static preview.
            blocks.append(preview_image(_upscale_preview(out_path)))
        return blocks
