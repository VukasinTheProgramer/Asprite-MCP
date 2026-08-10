import pytest
from mcp.client import Client

from aseprite_mcp.server import mcp

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path, monkeypatch, aseprite_exe):
    monkeypatch.setenv("ASEPRITE_PATH", str(aseprite_exe))
    monkeypatch.setenv("ASEPRITE_MCP_WORKSPACE", str(tmp_path))
    return tmp_path


async def test_set_palette_preset_changes_real_rendered_color(workspace):
    """Not just "does the call succeed" — confirms the palette actually
    landed in the saved sprite and index 1 is PICO-8's real ink color."""
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 4, "height": 4})
        out = await c.call_tool("set_palette", {"palette": "pico8"})
        assert not out.is_error
        assert "image" in [b.type for b in out.content]

    from aseprite_mcp.bridge.batch import BatchBridge
    from aseprite_mcp.discovery import find_aseprite

    b = BatchBridge(find_aseprite())
    result = b.execute(
        f"local spr = app.open('{workspace / 's.aseprite'}')\n"
        "local c = spr.palettes[1]:getColor(1)\n"
        "return { r = c.red, g = c.green, b = c.blue }"
    )
    assert (result["r"], result["g"], result["b"]) == (0x1D, 0x2B, 0x53)


async def test_set_palette_explicit_hex_list(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 4, "height": 4})
        out = await c.call_tool(
            "set_palette", {"palette": ["#000000", "#ff0000", "#00ff00"]}
        )
        assert not out.is_error
        assert "3 colors" in out.content[0].text


async def test_set_palette_preserve_indices_reports_real_state(workspace):
    """Regression: the returned table must reflect what's actually on disk,
    not what was requested — preserve_indices only writes past the old size,
    so indices 0/1 here keep their original color (M4 finding)."""
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 4, "height": 4})
        await c.call_tool("set_palette", {"palette": ["#ff0000", "#00ff00"]})
        out = await c.call_tool(
            "set_palette",
            {"palette": ["#111111", "#222222", "#0000ff"], "preserve_indices": True},
        )
        assert not out.is_error
        text = out.content[0].text
        assert "| 0 | #ff0000 |" in text  # untouched, NOT #111111
        assert "| 1 | #00ff00 |" in text  # untouched, NOT #222222
        assert "| 2 | #0000ff |" in text  # newly appended


async def test_set_palette_rejects_unknown_preset(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 4, "height": 4})
        out = await c.call_tool("set_palette", {"palette": "not_a_real_preset"})
        assert out.is_error
        assert "unknown_palette_preset" in out.content[0].text


async def test_set_palette_rejects_invalid_hex(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 4, "height": 4})
        out = await c.call_tool("set_palette", {"palette": ["not-a-color"]})
        assert out.is_error
        assert "invalid_hex_color" in out.content[0].text


async def test_get_ramp_produces_requested_step_count_and_swatch(workspace):
    # get_ramp itself needs no sprite/bridge call, but the server's lifespan
    # still resolves the Aseprite binary unconditionally on every Client
    # session — `workspace` (pulling in aseprite_exe) is what makes this test
    # skip cleanly rather than fail when no binary is found.
    async with Client(mcp) as c:
        out = await c.call_tool("get_ramp", {"base_color": "#854c30", "steps": 5})
        assert not out.is_error
        assert "image" in [b.type for b in out.content]
        hexes = out.content[0].text.split(": ", 1)[1].split(", ")
        assert len(hexes) == 5
        assert hexes[2] == "#854c30"  # middle step is the base color, unmodified


async def test_get_ramp_rejects_too_few_steps(workspace):
    async with Client(mcp) as c:
        out = await c.call_tool("get_ramp", {"base_color": "#854c30", "steps": 1})
        assert out.is_error
        assert "too_few_steps" in out.content[0].text
