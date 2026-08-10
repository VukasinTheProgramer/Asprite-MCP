import pytest

from aseprite_mcp.discovery import find_aseprite


@pytest.fixture(scope="session")
def aseprite_exe():
    """Skip integration tests cleanly when no binary is found (§11.6)."""
    try:
        return find_aseprite()
    except RuntimeError as e:
        pytest.skip(f"no Aseprite binary available: {e}")


@pytest.fixture
def read_pixels(aseprite_exe):
    """Read back a cel's pixels as a list of row-strings, e.g. ['0011100', ...].
    Bypasses the MCP layer entirely — for asserting on ground truth, since
    get_region_as_grid (the in-protocol way to read pixels back) doesn't
    exist until M3."""
    from aseprite_mcp.bridge.batch import BatchBridge
    from aseprite_mcp.validation import lua_str

    bridge = BatchBridge(aseprite_exe)

    def _read(path: str, width: int, height: int) -> list[str]:
        result = bridge.execute(
            f"local spr = app.open({lua_str(path)})\n"
            "local img = spr.layers[1]:cel(1).image\n"
            "local rows = {}\n"
            f"for row = 0, {height - 1} do\n"
            "  local s = ''\n"
            f"  for col = 0, {width - 1} do s = s .. tostring(img:getPixel(col, row)) end\n"
            "  rows[row + 1] = s\n"
            "end\n"
            "return rows"
        )
        return result

    return _read
