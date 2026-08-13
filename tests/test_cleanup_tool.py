import pytest
from mcp.client import Client

from aseprite_mcp.server import mcp

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path, monkeypatch, aseprite_exe):
    monkeypatch.setenv("ASEPRITE_PATH", str(aseprite_exe))
    monkeypatch.setenv("ASEPRITE_MCP_WORKSPACE", str(tmp_path))
    return tmp_path


# a solid block of index 1 with three stray index-2 specks around it
_SPECKED = "\n".join(
    [
        ".2........",
        "..........",
        "..1111....",
        "..1111...2",
        "..1111....",
        "..1111....",
        "..........",
        "....2.....",
        "..........",
        "..........",
    ]
)


async def _make_specked(c):
    await c.call_tool("create_sprite", {"name": "s", "width": 10, "height": 10})
    await c.call_tool("set_palette", {"palette": ["#000000", "#ff0000", "#00ff00"]})
    await c.call_tool("draw_grid", {"grid": _SPECKED, "preview": False})


async def test_cleanup_removes_orphans_and_leaves_the_block(workspace):
    async with Client(mcp) as c:
        await _make_specked(c)
        out = await c.call_tool(
            "cleanup", {"operations": ["remove_orphans"], "preview": False}
        )
        assert not out.is_error
        assert "remove_orphans: 3 pixels" in out.content[0].text

        grid = await c.call_tool("get_region_as_grid", {})
        rows = grid.structured_content["grid"].splitlines()
        assert not any("2" in r for r in rows)
        assert rows[2] == "..1111...."  # the real region is untouched


async def test_cleanup_is_idempotent(workspace):
    async with Client(mcp) as c:
        await _make_specked(c)
        await c.call_tool("cleanup", {"preview": False})
        out = await c.call_tool("cleanup", {"preview": False})
        assert not out.is_error
        assert "Already clean" in out.content[0].text


async def test_cleanup_rejects_unknown_operation(workspace):
    async with Client(mcp) as c:
        await _make_specked(c)
        out = await c.call_tool("cleanup", {"operations": ["deblur"]})
        assert out.is_error
        assert "unknown_cleanup_operation" in out.content[0].text
