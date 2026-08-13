import pytest
from mcp.client import Client

from aseprite_mcp.server import mcp

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path, monkeypatch, aseprite_exe):
    monkeypatch.setenv("ASEPRITE_PATH", str(aseprite_exe))
    monkeypatch.setenv("ASEPRITE_MCP_WORKSPACE", str(tmp_path))
    return tmp_path


async def _pixel(workspace, aseprite_exe):
    from aseprite_mcp.bridge.batch import BatchBridge

    b = BatchBridge(aseprite_exe)
    return b.execute(
        f"local spr = app.open('{workspace / 's.aseprite'}')\n"
        "return { p = spr.layers[1]:cel(1).image:getPixel(0,0) }"
    )["p"]


async def test_run_lua_returns_the_scripts_result(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 4, "height": 4})
        out = await c.call_tool(
            "run_lua",
            {"script": "local spr = J.sprite(path)\nreturn { w = spr.width }", "preview": False},
        )
        assert not out.is_error
        assert '"w": 4' in out.content[0].text


async def test_run_lua_mutation_persists_to_disk(workspace, aseprite_exe):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 4, "height": 4})
        out = await c.call_tool(
            "run_lua",
            {
                "script": (
                    "local spr = J.sprite(path)\n"
                    "local cel = spr.layers[1]:cel(1)\n"
                    "local img = cel.image:clone()\n"
                    "img:drawPixel(0,0,1)\n"
                    "cel.image = img\n"
                    "J.save(spr)\n"
                    "return { ok = true }"
                ),
                "preview": False,
            },
        )
        assert not out.is_error
    assert await _pixel(workspace, aseprite_exe) == 1


async def test_run_lua_sandbox_blocks_shell_escape(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 4, "height": 4})
        out = await c.call_tool("run_lua", {"script": 'os.execute("echo pwned")', "preview": False})
        assert out.is_error
        assert "os.execute is disabled" in out.content[0].text


async def test_run_lua_logs_every_script(workspace):
    # logs/runtime/previews live under the fixed ~/.aseprite-mcp root
    # (config.py) — only `workspace` is overridden by the fixture, so this
    # checks the real log file rather than a test-isolated one.
    import uuid
    from pathlib import Path

    marker = f"UNIQUE_MARKER_{uuid.uuid4().hex}"
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 4, "height": 4})
        await c.call_tool(
            "run_lua",
            {"script": f"-- {marker}\nreturn {{ ok = true }}", "preview": False},
        )
    # run_lua used to hand-append to its own run_lua.log, which had no rotation
    # and was the only thing in the project that logged at all. It now goes
    # through the shared rotating logger as a JSON record.
    import json

    log_path = Path("~/.aseprite-mcp/logs/aseprite-mcp.log").expanduser()
    records = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    mine = [r for r in records if marker in r.get("context", {}).get("script", "")]
    assert mine, f"no run_lua record carrying {marker}"
    assert mine[-1]["msg"] == "run_lua"
    assert mine[-1]["context"]["sprite"].endswith("s.aseprite")


async def test_undo_redo_full_cycle_matches_real_pixel_state(workspace, aseprite_exe):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 4, "height": 4})
        await c.call_tool("draw_grid", {"grid": "1", "preview": False})
        assert await _pixel(workspace, aseprite_exe) == 1

        await c.call_tool("draw_grid", {"grid": "2", "preview": False})
        assert await _pixel(workspace, aseprite_exe) == 2

        out = await c.call_tool("undo", {})
        assert not out.is_error
        assert await _pixel(workspace, aseprite_exe) == 1

        out = await c.call_tool("undo", {})
        assert not out.is_error
        assert await _pixel(workspace, aseprite_exe) == 0

        out = await c.call_tool("undo", {})
        assert out.is_error
        assert "nothing_to_undo" in out.content[0].text

        out = await c.call_tool("redo", {"steps": 2})
        assert not out.is_error
        assert await _pixel(workspace, aseprite_exe) == 2


async def test_undo_rejects_nonpositive_steps(workspace):
    """Regression: range(-5) is empty in Python, so a naive loop silently
    treated negative steps as 'zero steps taken', surfacing a misleading
    nothing_to_undo error instead of rejecting the bad input directly
    (found via adversarial testing, M9)."""
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 4, "height": 4})
        out = await c.call_tool("undo", {"steps": -5})
        assert out.is_error
        assert "greater than or equal to 1" in out.content[0].text


async def test_new_edit_after_undo_clears_redo_history(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 4, "height": 4})
        await c.call_tool("draw_grid", {"grid": "1", "preview": False})
        await c.call_tool("undo", {})
        await c.call_tool("draw_grid", {"grid": "2", "preview": False})

        out = await c.call_tool("redo", {})
        assert out.is_error
        assert "nothing_to_redo" in out.content[0].text
