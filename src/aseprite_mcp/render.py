import io
import uuid
from pathlib import Path

from mcp.server.mcpserver import Image as MCPImage
from PIL import Image

from .bridge.base import AsepriteBridge
from .validation import lua_str

_MAX_DIM = 1024


def render_preview(
    bridge: AsepriteBridge,
    previews_dir: Path,
    sprite_path: str,
    frame: int = 1,
    scale: int | str = "auto",
    max_dim: int = 512,
) -> tuple[bytes, dict]:
    """Render a sprite frame to an upscaled PNG. NEAREST upscale always —
    bilinear makes the model hallucinate anti-aliasing that isn't there
    (CLAUDE.md #10). A raw 32x32 PNG is close to unreadable to a vision
    encoder, hence the upscale (#11)."""
    tmp = previews_dir / f"{uuid.uuid4().hex}.png"
    bridge.execute(
        f"local spr = J.sprite({lua_str(sprite_path)})\n"
        f"if not spr.frames[{frame}] then error('frame_out_of_range: {frame}') end\n"
        # RGB, not spr.colorMode: a standalone Image carries no palette, so saving an
        # indexed one to PNG resolves every index to black. drawSprite composites
        # through the sprite's palette when the target is RGB.
        "local img = Image(spr.width, spr.height, ColorMode.RGB)\n"
        f"img:drawSprite(spr, {frame})\n"
        f"img:saveAs({lua_str(str(tmp))})\n"
        "return { w = spr.width, h = spr.height }"
    )
    try:
        im = Image.open(tmp).convert("RGBA")
    finally:
        tmp.unlink(missing_ok=True)  # previews/ is ephemeral (§0.4) — don't accumulate

    native_w, native_h = im.width, im.height
    if scale == "auto":
        scale = max(1, min(16, max_dim // max(native_w, native_h)))
    scale = int(scale)
    im = im.resize((native_w * scale, native_h * scale), Image.Resampling.NEAREST)

    if im.width > _MAX_DIM or im.height > _MAX_DIM:
        # Cap output size (#11) even if a caller passes an oversized explicit scale.
        clamp = min(_MAX_DIM / im.width, _MAX_DIM / im.height)
        im = im.resize((int(im.width * clamp), int(im.height * clamp)), Image.Resampling.NEAREST)

    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue(), {"scale": scale, "native": (native_w, native_h)}


def preview_image(png: bytes) -> MCPImage:
    return MCPImage(data=png, format="png")


def render_palette_swatch(colors: list[str], swatch: int = 32) -> bytes:
    """A horizontal strip, one square per color. No Aseprite round-trip —
    pure Pillow. "The model choosing colors it can see is materially better
    than it choosing from hex strings" (§7.2)."""
    from PIL import ImageDraw

    from .validation import hex_to_rgba

    im = Image.new("RGBA", (swatch * len(colors), swatch), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    for i, hex_color in enumerate(colors):
        draw.rectangle([i * swatch, 0, (i + 1) * swatch - 1, swatch - 1], fill=hex_to_rgba(hex_color))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def emit(
    bridge: AsepriteBridge,
    previews_dir: Path,
    sprite_path: str | None,
    summary: str,
    preview: bool = True,
) -> list[str | MCPImage]:
    """Every mutating tool's return path (CLAUDE.md #16). `structured_content`
    is None on any result carrying an Image (#26) — this is why mutating
    tools return list[str | MCPImage] instead of a dict with a preview field."""
    blocks: list[str | MCPImage] = [summary]
    if preview and sprite_path:
        png, meta = render_preview(bridge, previews_dir, sprite_path)
        w, h = meta["native"]
        blocks.append(f"preview: {w}x{h} at {meta['scale']}x")
        blocks.append(preview_image(png))
    return blocks
