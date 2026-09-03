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


async def test_explicit_sprite_param_resolves_inside_workspace(workspace):
    """resolve_sprite (state.py) used to return an explicit `sprite` argument
    verbatim instead of routing it through safe_path the way create_sprite
    resolves `name` when it first writes the file. A bare sprite="knight"
    reached Lua unresolved and Aseprite opened it relative to the server
    process's cwd instead of the workspace, so every tool's sprite= param
    missed every sprite that wasn't already the active one."""
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "knight", "width": 8, "height": 8})
        # A second sprite becomes active, so 'knight' is no longer session.active —
        # only the sprite= param itself can find it now.
        await c.call_tool("create_sprite", {"name": "other", "width": 8, "height": 8})

        out = await c.call_tool("get_sprite_info", {"sprite": "knight"})
        assert not out.is_error
        assert out.structured_content["width"] == 8

        out_ext = await c.call_tool("get_sprite_info", {"sprite": "knight.aseprite"})
        assert not out_ext.is_error
        assert out_ext.structured_content["width"] == 8
