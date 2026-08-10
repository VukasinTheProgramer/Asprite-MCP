import pytest
from mcp.client import Client

from aseprite_mcp.server import mcp

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path, monkeypatch, aseprite_exe):
    monkeypatch.setenv("ASEPRITE_PATH", str(aseprite_exe))
    monkeypatch.setenv("ASEPRITE_MCP_WORKSPACE", str(tmp_path))
    return tmp_path


async def test_layers_add_set_list(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 8, "height": 8})
        out = await c.call_tool("layers", {"action": "add", "name": "details"})
        assert not out.is_error

        out = await c.call_tool(
            "layers", {"action": "set", "name": "details", "opacity": 128, "visible": False}
        )
        assert not out.is_error

        out = await c.call_tool("layers", {"action": "list"})
        text = out.content[0].text
        assert "| details | False | 128 | False |" in text


async def test_layers_missing_parameter_names_it(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 8, "height": 8})
        out = await c.call_tool("layers", {"action": "delete"})
        assert out.is_error
        assert "missing_parameter" in out.content[0].text
        assert "name" in out.content[0].text


async def test_layers_merge_down_bottom_layer_raises_not_silent_noop(workspace):
    """Regression: app.command.MergeDownLayer() no-ops silently on a layer
    with nothing below it. Must be a real error, not a false success
    (M5 finding)."""
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 8, "height": 8})
        out = await c.call_tool("layers", {"action": "add", "name": "top"})
        assert not out.is_error
        # "Layer 1" is now the bottom-most layer — nothing below it
        out = await c.call_tool("layers", {"action": "merge_down", "name": "Layer 1"})
        assert out.is_error
        assert "nothing_to_merge" in out.content[0].text


async def test_layers_duplicate_increases_count(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 8, "height": 8})
        out = await c.call_tool("layers", {"action": "duplicate", "name": "Layer 1"})
        assert not out.is_error
        out = await c.call_tool("layers", {"action": "list"})
        rows = [line for line in out.content[0].text.splitlines() if line.startswith("| Layer")]
        assert rows == ["| Layer 1 | True | 255 | False |", "| Layer 1 Copy | True | 255 | False |"]


async def test_frames_add_list_set_duration(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 8, "height": 8})
        out = await c.call_tool("frames", {"action": "add", "duration": 0.3})
        assert not out.is_error
        assert "2 frames total" in out.content[0].text

        out = await c.call_tool("frames", {"action": "set_duration", "index": 2, "duration": 0.5})
        assert not out.is_error

        out = await c.call_tool("frames", {"action": "list"})
        assert "| 2 | 0.5s |" in out.content[0].text


async def test_frames_duplicate_and_delete(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 8, "height": 8})
        out = await c.call_tool("frames", {"action": "duplicate", "index": 1})
        assert not out.is_error
        assert "2 frames total" in out.content[0].text

        out = await c.call_tool("frames", {"action": "delete", "index": 2})
        assert not out.is_error
        assert "1 frames remain" in out.content[0].text


async def test_frames_rejects_out_of_range_index(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 8, "height": 8})
        out = await c.call_tool("frames", {"action": "delete", "index": 99})
        assert out.is_error
        assert "frame_out_of_range" in out.content[0].text


async def test_tags_add_list_rename(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 8, "height": 8})
        await c.call_tool("frames", {"action": "add"})
        out = await c.call_tool(
            "tags",
            {"action": "add", "name": "walk", "from_frame": 1, "to_frame": 2, "direction": "pingpong"},
        )
        assert not out.is_error

        out = await c.call_tool("tags", {"action": "list"})
        assert "| walk | 1 | 2 |" in out.content[0].text

        out = await c.call_tool("tags", {"action": "rename", "name": "walk", "new_name": "run"})
        assert not out.is_error

        out = await c.call_tool("tags", {"action": "list"})
        assert "run" in out.content[0].text
        assert "walk" not in out.content[0].text


async def test_tags_delete_unknown_name_errors(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 8, "height": 8})
        out = await c.call_tool("tags", {"action": "delete", "name": "nonexistent"})
        assert out.is_error
        assert "tag_not_found" in out.content[0].text
