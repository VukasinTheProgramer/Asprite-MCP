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


@pytest.fixture
def split_image(workspace):
    """White background, red left half, blue right half — spatial fidelity
    through crop/downscale/quantize is verifiable by checking the split
    survived, not just that the call succeeded."""
    im = Image.new("RGB", (100, 100), (255, 255, 255))
    for y in range(20, 80):
        for x in range(20, 50):
            im.putpixel((x, y), (200, 30, 30))
        for x in range(50, 80):
            im.putpixel((x, y), (30, 30, 200))
    path = workspace / "ref.png"
    im.save(path)
    return "ref.png"


async def test_import_reference_preserves_spatial_layout(workspace, split_image):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 10, "height": 10})
        await c.call_tool("set_palette", {"palette": ["#ffffff", "#c81e1e", "#1e1ec8"]})
        out = await c.call_tool("import_reference", {"image_path": split_image})
        assert not out.is_error
        grid = out.content[0].text.split("\n", 1)[1]
        rows = grid.splitlines()
        assert rows[0][0] == "1"  # left half quantized to red (palette index 1)
        assert rows[0][-1] == "2"  # right half quantized to blue (palette index 2)


async def test_import_reference_creates_locked_and_editable_layers(workspace, split_image):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 10, "height": 10})
        await c.call_tool("set_palette", {"palette": ["#ffffff", "#c81e1e", "#1e1ec8"]})
        out = await c.call_tool("import_reference", {"image_path": split_image, "opacity": 100})
        assert not out.is_error

    from aseprite_mcp.bridge.batch import BatchBridge
    from aseprite_mcp.discovery import find_aseprite

    b = BatchBridge(find_aseprite())
    result = b.execute(
        f"local spr = app.open('{workspace / 's.aseprite'}')\n"
        "local info = {}\n"
        "for i, l in ipairs(spr.layers) do info[i] = {name=l.name, editable=l.isEditable, opacity=l.opacity} end\n"
        "return info"
    )
    by_name = {layer["name"]: layer for layer in result}
    assert by_name["reference"]["editable"] is False
    assert by_name["reference"]["opacity"] == 100
    assert by_name["reference_quantized"]["editable"] is True


async def test_import_reference_mode_trace_only_creates_one_layer(workspace, split_image):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 10, "height": 10})
        await c.call_tool("set_palette", {"palette": ["#ffffff", "#c81e1e", "#1e1ec8"]})
        out = await c.call_tool("import_reference", {"image_path": split_image, "mode": "trace"})
        assert not out.is_error
        assert "1 layer" in out.content[0].text


async def test_import_reference_missing_file_errors(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 10, "height": 10})
        out = await c.call_tool("import_reference", {"image_path": "nope.png"})
        assert out.is_error
        assert "reference_not_found" in out.content[0].text


async def test_import_reference_outside_workspace_blocked_by_default(workspace, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "external.png"
    Image.new("RGB", (10, 10), (255, 0, 0)).save(outside)
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 10, "height": 10})
        out = await c.call_tool("import_reference", {"image_path": str(outside)})
        assert out.is_error
        assert "path_outside_workspace" in out.content[0].text


async def test_import_reference_outside_workspace_allowed_with_opt_in(workspace, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "external.png"
    Image.new("RGB", (10, 10), (255, 0, 0)).save(outside)
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 10, "height": 10})
        await c.call_tool("set_palette", {"palette": ["#ffffff", "#ff0000"]})
        out = await c.call_tool(
            "import_reference", {"image_path": str(outside), "allow_external_path": True}
        )
        assert not out.is_error
