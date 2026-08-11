"""End-to-end asset tests: build a real 32x32 dirt tile through the MCP
tools and assert on the pixels that actually land.

Unlike the per-tool suites (8x8 toys, one tool per test), these drive the
whole chain the way a model would — create -> palette -> base fill -> detail
overlays -> read back -> export — so a regression anywhere in that path shows
up as a wrong tile rather than a passing unit test. Skips without an Aseprite
binary, same as every other integration test.
"""

import pytest
from mcp.client import Client
from PIL import Image

from aseprite_mcp.server import mcp

pytestmark = pytest.mark.asyncio

# 0 transparent (never drawn), 1 dark, 2 mid, 3 light, 4 pebble shadow, 5 pebble highlight
DIRT_PALETTE = ["#000000", "#6b4a2f", "#8b5e3c", "#a97c50", "#4a3423", "#c49a6c"]

# 32x32 mottled earth: mostly index 2, speckled with 1 and 3 so the tile
# doesn't read as a flat square.
DIRT_BASE = """\
22322122322232122232212232223212
32223222123222322212232223122232
22132232221322322322122322322123
23222122232223122232221232122322
22322322122322232122322322232212
12223212232122322232122232223122
22322232322232212322232122322232
23122322212322322122232232122322
22232122322122232322122322232122
32223222122322122232223212232223
22322312232223222122322122322232
22123222321222322322232232122122
23222122232322122232122322232322
22322232122122322122322232122232
12232122322322232322232122322322
22322232232122122232122232232122
32122322122322322122322322122232
22232232322232122322232122322322
22322122232122232232122322232212
12322232122322322322232232122322
22122322322232122122322122322232
23222122232322322232232322232122
22322232122122232322122232122322
32232122322322122122322322232232
22122322232232322232232122322122
22322232122122322322122322232322
12232122322322232122322232122232
22322232232322122232232122322322
23122322122122322322122322232122
22322232322232232122322232122232
32122122232122122232232122322322
22232322122322322322122322232212"""

# 4x3 pebble. '.' is "leave the pixel alone", not "erase" — the base fill
# shows through, which is what makes this a legal overlay.
PEBBLE = """\
.44.
4554
.11."""
PEBBLE_AT = [(4, 6), (20, 18), (11, 24)]


@pytest.fixture
def workspace(tmp_path, monkeypatch, aseprite_exe):
    monkeypatch.setenv("ASEPRITE_PATH", str(aseprite_exe))
    monkeypatch.setenv("ASEPRITE_MCP_WORKSPACE", str(tmp_path))
    return tmp_path


async def _build_dirt_tile(c):
    """Create the tile exactly as a model would, asserting each step landed."""
    for name, args in [
        ("create_sprite", {"name": "dirt", "width": 32, "height": 32}),
        ("set_palette", {"palette": DIRT_PALETTE, "preview": False}),
        ("draw_grid", {"grid": DIRT_BASE, "preview": False}),
    ]:
        out = await c.call_tool(name, args)
        assert not out.is_error, f"{name} failed: {out.content[0].text}"

    for x, y in PEBBLE_AT:
        out = await c.call_tool("draw_grid", {"grid": PEBBLE, "x": x, "y": y, "preview": False})
        assert not out.is_error, f"pebble at {(x, y)} failed: {out.content[0].text}"


async def _read_tile(c) -> list[str]:
    out = await c.call_tool("get_region_as_grid", {})
    assert not out.is_error
    return out.structured_content["grid"].split("\n")


async def test_dirt_tile_is_fully_opaque_32x32(workspace):
    """A terrain tile with a transparent pixel in it is a bug you only see
    in-game. The base fill must cover every pixel."""
    async with Client(mcp) as c:
        await _build_dirt_tile(c)
        rows = await _read_tile(c)

    assert len(rows) == 32
    assert {len(r) for r in rows} == {32}
    holes = [(x, y) for y, row in enumerate(rows) for x, ch in enumerate(row) if ch == "."]
    assert not holes, f"transparent pixels in tile at {holes[:10]}"


async def test_pebbles_land_at_their_offsets_without_erasing_base(workspace):
    """draw_grid x/y offset + '.' passthrough, in one assert per pebble."""
    async with Client(mcp) as c:
        await _build_dirt_tile(c)
        rows = await _read_tile(c)
        base = DIRT_BASE.split("\n")

    for x, y in PEBBLE_AT:
        assert rows[y + 1][x : x + 4] == "4554", f"pebble body wrong at {(x, y)}"
        assert rows[y][x + 1 : x + 3] == "44", f"pebble top wrong at {(x, y)}"
        # the two '.' corners of the pebble's top row kept the base fill
        assert rows[y][x] == base[y][x], f"pebble erased base at {(x, y)}"
        assert rows[y][x + 3] == base[y][x + 3], f"pebble erased base at {(x + 3, y)}"


async def test_dirt_tile_uses_only_its_palette(workspace):
    """Catches palette drift: every index in the tile must be one we defined."""
    async with Client(mcp) as c:
        await _build_dirt_tile(c)
        rows = await _read_tile(c)

    assert set("".join(rows)) <= set("12345")


async def test_dirt_tile_exports_to_a_real_32x32_png(workspace):
    async with Client(mcp) as c:
        await _build_dirt_tile(c)
        out = await c.call_tool("export", {"format": "png", "preview": False})
        assert not out.is_error, out.content[0].text

    im = Image.open(workspace / "dirt.png").convert("RGBA")
    assert im.size == (32, 32)
    assert im.getbbox() is not None, "exported a blank tile"
    # 5 drawn indices, opaque everywhere — no more distinct colors than that.
    colors = im.getcolors(maxcolors=256)
    assert colors is not None and 1 < len(colors) <= 5


async def test_dirt_tile_survives_undo_of_the_last_pebble(workspace):
    """The snapshot history is the only undo that works in batch mode
    (rule 35) — verify it restores pixels, not just exits 0."""
    async with Client(mcp) as c:
        await _build_dirt_tile(c)
        after = await _read_tile(c)

        out = await c.call_tool("undo", {"preview": False})
        assert not out.is_error, out.content[0].text
        undone = await _read_tile(c)

        out = await c.call_tool("redo", {"preview": False})
        assert not out.is_error, out.content[0].text
        redone = await _read_tile(c)

    x, y = PEBBLE_AT[-1]
    assert undone[y + 1][x : x + 4] != "4554", "undo left the last pebble in place"
    assert redone == after, "redo did not restore the tile"
