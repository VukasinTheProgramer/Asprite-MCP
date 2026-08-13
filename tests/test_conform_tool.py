import pytest
from mcp.client import Client
from PIL import Image

from aseprite_mcp.server import mcp

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path, monkeypatch, aseprite_exe):
    # Client(mcp) resolves the Aseprite binary in its lifespan unconditionally,
    # even for tools that never touch the bridge (CLAUDE.md testing notes) --
    # the aseprite_exe fixture dependency is what makes this skip cleanly
    # instead of erroring when no binary is installed.
    monkeypatch.setenv("ASEPRITE_PATH", str(aseprite_exe))
    monkeypatch.setenv("ASEPRITE_MCP_WORKSPACE", str(tmp_path))
    return tmp_path


def _upscaled_checkerboard(workspace, cell: int = 8, cells: int = 4) -> str:
    """A cells x cells checkerboard of two flat colors, upscaled `cell`x with
    NEAREST -- a clean synthetic stand-in for a real "8x upscale of pixel art"
    reference image."""
    small = Image.new("RGB", (cells, cells))
    for y in range(cells):
        for x in range(cells):
            small.putpixel((x, y), (200, 30, 30) if (x + y) % 2 == 0 else (30, 30, 200))
    big = small.resize((cells * cell, cells * cell), Image.Resampling.NEAREST)
    path = workspace / "checker.png"
    big.save(path)
    return "checker.png"


async def test_detect_grid_finds_upscale_factor(workspace):
    image_path = _upscaled_checkerboard(workspace, cell=8, cells=4)
    async with Client(mcp) as c:
        out = await c.call_tool("detect_grid", {"image_path": image_path})
        assert not out.is_error
        info = out.structured_content
        assert info["cell_w"] == 8
        assert info["cell_h"] == 8
        assert info["is_pixel_art"] is True


async def test_conform_image_without_sprite_returns_before_after(workspace):
    image_path = _upscaled_checkerboard(workspace, cell=8, cells=4)
    async with Client(mcp) as c:
        out = await c.call_tool(
            "conform_image",
            {
                "image_path": image_path,
                "target_size": [4, 4],
                "palette": ["#c81e1e", "#1e1ec8"],
                "import_to_sprite": False,
            },
        )
        assert not out.is_error
        texts = [b.text for b in out.content if hasattr(b, "text")]
        assert any("Conformed" in t for t in texts)
        images = [b for b in out.content if not hasattr(b, "text")]
        assert len(images) == 2  # before + after


async def test_conform_image_writes_into_matching_sprite(workspace, read_pixels):
    image_path = _upscaled_checkerboard(workspace, cell=8, cells=4)
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 4, "height": 4})
        out = await c.call_tool(
            "conform_image",
            {
                "image_path": image_path,
                "target_size": [4, 4],
                "palette": ["#c81e1e", "#1e1ec8"],
                "preview": False,
            },
        )
        assert not out.is_error
        assert "Written to sprite" in out.content[0].text

    rows = read_pixels(str(workspace / "s.aseprite"), 4, 4)
    used = {ch for row in rows for ch in row}
    assert used <= {"0", "1"}  # only the two palette indices, nothing off-palette


async def test_conform_image_rejects_canvas_size_mismatch(workspace):
    image_path = _upscaled_checkerboard(workspace, cell=8, cells=4)
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 8, "height": 8})
        out = await c.call_tool(
            "conform_image", {"image_path": image_path, "target_size": [4, 4]}
        )
        assert out.is_error
        assert "canvas_size_mismatch" in out.content[0].text


async def test_conform_image_requires_palette_without_active_sprite(workspace):
    image_path = _upscaled_checkerboard(workspace, cell=8, cells=4)
    async with Client(mcp) as c:
        out = await c.call_tool(
            "conform_image",
            {"image_path": image_path, "target_size": [4, 4], "import_to_sprite": False},
        )
        assert out.is_error
        assert "no_palette" in out.content[0].text
