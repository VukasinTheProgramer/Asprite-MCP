import json

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


async def _make_two_frame_sprite(c):
    await c.call_tool("create_sprite", {"name": "s", "width": 8, "height": 8})
    await c.call_tool("set_palette", {"palette": ["#ffffff", "#ff0000"]})
    await c.call_tool("draw_grid", {"grid": "1", "preview": False})
    await c.call_tool("frames", {"action": "add"})


async def test_export_png_writes_a_real_readable_image(workspace):
    async with Client(mcp) as c:
        await _make_two_frame_sprite(c)
        out = await c.call_tool("export", {"format": "png", "frame": 1})
        assert not out.is_error
        assert "image" in [b.type for b in out.content]

    im = Image.open(workspace / "s.png")
    assert im.size == (8, 8)


async def test_export_gif_writes_a_real_file(workspace):
    async with Client(mcp) as c:
        await _make_two_frame_sprite(c)
        out = await c.call_tool("export", {"format": "gif"})
        assert not out.is_error
    assert (workspace / "s.gif").exists()
    assert (workspace / "s.gif").stat().st_size > 0


async def test_export_spritesheet_writes_image_and_valid_json(workspace):
    async with Client(mcp) as c:
        await _make_two_frame_sprite(c)
        out = await c.call_tool("export", {"format": "spritesheet", "sheet_type": "horizontal"})
        assert not out.is_error

    sheet = workspace / "s-sheet.png"
    data = workspace / "s-sheet.json"
    assert sheet.exists()
    im = Image.open(sheet)
    assert im.size == (16, 8)  # two 8x8 frames laid out horizontally

    meta = json.loads(data.read_text())
    assert len(meta["frames"]) == 2


async def test_export_png_frame_is_one_based(workspace):
    """Regression: --frame-range is 0-based, `frame` is 1-based. Sending
    frame,frame instead of frame-1,frame-1 exported the *next* frame — here
    the empty frame 2 — as a fully blank PNG with exit 0."""
    async with Client(mcp) as c:
        await _make_two_frame_sprite(c)  # frame 1 painted red, frame 2 empty
        out = await c.call_tool("export", {"format": "png", "frame": 1})
        assert not out.is_error

    # getbbox() is None only when every pixel is 0 — i.e. a fully blank export.
    im = Image.open(workspace / "s.png").convert("RGBA")
    assert im.getbbox() is not None, "frame 1 exported blank — off-by-one"


async def test_export_png_rejects_out_of_range_frame(workspace):
    """Regression: an out-of-range --frame-range exits 0 and writes a blank
    PNG rather than erroring, so the file-exists check can't catch it. Must
    be rejected before ever invoking the CLI."""
    async with Client(mcp) as c:
        await _make_two_frame_sprite(c)
        out = await c.call_tool("export", {"format": "png", "frame": 99})
        assert out.is_error
        assert "frame_out_of_range" in out.content[0].text


async def test_export_custom_path_respects_workspace_jail(workspace):
    async with Client(mcp) as c:
        await _make_two_frame_sprite(c)
        out = await c.call_tool("export", {"format": "png", "path": "../outside.png"})
        assert out.is_error
        assert "path_outside_workspace" in out.content[0].text


async def test_export_default_path_derived_from_sprite_name(workspace):
    async with Client(mcp) as c:
        await _make_two_frame_sprite(c)
        out = await c.call_tool("export", {"format": "png"})
        assert not out.is_error
        assert "s.png" in out.content[0].text
    assert (workspace / "s.png").exists()
