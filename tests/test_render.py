import base64
import io

import pytest
from mcp.client import Client
from PIL import Image

from aseprite_mcp.server import mcp

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path, monkeypatch, aseprite_exe):
    monkeypatch.setenv("ASEPRITE_PATH", str(aseprite_exe))
    monkeypatch.setenv("ASEPRITE_MCP_WORKSPACE", str(tmp_path))
    return tmp_path


async def test_indexed_sprite_preview_is_not_solid_black(workspace):
    """render_preview built its temp Image in spr.colorMode. For an indexed
    sprite — the mode create_sprite defaults to and the README recommends —
    that's a palette-less Image, so saving it to PNG resolved every pixel
    index to black regardless of what was actually drawn. Paint a
    non-transparent, non-black color and check the returned preview PNG
    actually shows it."""
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 4, "height": 4})
        await c.call_tool("set_palette", {"palette": ["#000000", "#ff0000"]})
        out = await c.call_tool("draw_grid", {"grid": "1111\n1111\n1111\n1111"})
        assert not out.is_error

    images = [b for b in out.content if b.type == "image"]
    assert images, "draw_grid should return a preview image block"
    png = base64.b64decode(images[0].data)
    im = Image.open(io.BytesIO(png)).convert("RGB")
    assert im.getpixel((im.width // 2, im.height // 2)) == (255, 0, 0)
