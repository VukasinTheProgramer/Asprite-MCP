import pytest
from mcp.client import Client

from aseprite_mcp.server import mcp

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path, monkeypatch, aseprite_exe):
    monkeypatch.setenv("ASEPRITE_PATH", str(aseprite_exe))
    monkeypatch.setenv("ASEPRITE_MCP_WORKSPACE", str(tmp_path))
    return tmp_path


async def test_create_sprite_writes_a_real_file(workspace):
    async with Client(mcp) as c:
        out = await c.call_tool("create_sprite", {"name": "knight", "width": 32, "height": 32})
        assert not out.is_error
        assert (workspace / "knight.aseprite").exists()


async def test_duplicate_name_is_model_visible_error(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "knight", "width": 32, "height": 32})
        out = await c.call_tool("create_sprite", {"name": "knight", "width": 32, "height": 32})
        assert out.is_error  # NOT a dict returned as success — see CLAUDE.md #22
        assert "sprite_exists" in out.content[0].text


async def test_overwrite_true_replaces_it(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "knight", "width": 16, "height": 16})
        out = await c.call_tool(
            "create_sprite",
            {"name": "knight", "width": 32, "height": 32, "overwrite": True},
        )
        assert not out.is_error


async def test_oversized_dimension_rejected_by_schema(workspace):
    async with Client(mcp) as c:
        out = await c.call_tool("create_sprite", {"name": "huge", "width": 5000, "height": 32})
        assert out.is_error
        assert not (workspace / "huge.aseprite").exists()
