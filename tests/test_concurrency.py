"""Concurrent tool calls must not lose writes.

CLAUDE.md #2 requires one lock; #30 says it is load-bearing, not defensive,
because tools are plain `def` and the SDK runs each on a worker thread. Neither
was implemented for four reviews. The failure was silent: four concurrent
draw_grid calls landed one and discarded three, every one returning success, and
the preview handed back to the model showed a sprite that had already lost the
other edits.

Every command is open -> mutate -> save on the whole file, so overlapping calls
are a classic lost update. Only a concurrent test sees it; nothing about reading
the code suggests it.
"""

import asyncio

import pytest
from mcp.client import Client

from aseprite_mcp.server import mcp

pytestmark = pytest.mark.asyncio

_PALETTE = ["#000000", "#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff"]


@pytest.fixture
def workspace(tmp_path, monkeypatch, aseprite_exe):
    monkeypatch.setenv("ASEPRITE_PATH", str(aseprite_exe))
    monkeypatch.setenv("ASEPRITE_MCP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("ASEPRITE_MCP_STYLES", str(tmp_path / "st"))
    return tmp_path


async def _grid(c) -> list[str]:
    out = await c.call_tool("get_region_as_grid", {})
    return out.structured_content["grid"].split("\n")


async def test_concurrent_draws_all_land(workspace):
    """The original repro: four writers, four distinct rows."""
    rows = [("1", 0), ("2", 2), ("3", 4), ("4", 6)]
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 8, "height": 8, "preview": False})
        await c.call_tool("set_palette", {"palette": _PALETTE, "preview": False})

        results = await asyncio.gather(*[
            c.call_tool("draw_grid", {"grid": ch * 8, "y": y, "preview": False})
            for ch, y in rows
        ])
        assert not any(r.is_error for r in results)
        grid = await _grid(c)

    lost = [y for ch, y in rows if grid[y] != ch * 8]
    assert not lost, f"rows {lost} were silently discarded:\n" + "\n".join(grid)


async def test_many_concurrent_draws_all_land(workspace):
    """Wider than the repro — a lost update gets likelier with more writers, so
    a fix that only narrows the window would show up here."""
    n = 12
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 12, "height": 12, "preview": False})
        await c.call_tool("set_palette", {"palette": _PALETTE, "preview": False})

        await asyncio.gather(*[
            c.call_tool("draw_grid", {"grid": "1" * 12, "y": y, "preview": False})
            for y in range(n)
        ])
        grid = await _grid(c)

    missing = [y for y in range(n) if grid[y] != "1" * 12]
    assert not missing, f"{len(missing)}/{n} rows lost: {missing}"


async def test_a_read_modify_write_tool_does_not_clobber_a_concurrent_draw(workspace):
    """`cleanup` reads pixels, computes in numpy, then writes back — two separate
    bridge calls. Locking each call alone leaves a window where a draw landing
    between them is overwritten by cleanup's stale result, so those tools hold
    `bridge.serialized()` across the pair."""
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 16, "height": 16, "preview": False})
        await c.call_tool("set_palette", {"palette": _PALETTE, "preview": False})
        await c.call_tool(
            "draw_grid", {"grid": "\n".join("1" * 16 for _ in range(16)), "preview": False}
        )

        # run it several times: this is a race, and one pass proves little
        for attempt in range(4):
            await c.call_tool("draw_grid", {"grid": "1" * 16, "y": 8, "preview": False})
            await asyncio.gather(
                c.call_tool("cleanup", {"preview": False}),
                c.call_tool("draw_grid", {"grid": "2" * 16, "y": 8, "preview": False}),
            )
            grid = await _grid(c)
            assert grid[8] == "2" * 16, f"attempt {attempt}: draw clobbered, row 8 = {grid[8]}"


async def test_the_bridge_serializes_and_the_span_is_reentrant(workspace):
    """serialized() must not deadlock when the tool inside it calls execute()."""
    from aseprite_mcp.bridge.batch import BatchBridge
    from aseprite_mcp.discovery import find_aseprite

    b = BatchBridge(find_aseprite())
    with b.serialized():
        with b.serialized():                       # reentrant
            assert b.execute("return { ok = true }")["ok"] is True
