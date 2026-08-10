import pytest

from aseprite_mcp.bridge.batch import BatchBridge


@pytest.fixture
def bridge(aseprite_exe):
    return BatchBridge(aseprite_exe)


def test_encode_handles_newlines_tabs_and_quotes(bridge):
    """Regression: %q produces re-loadable Lua (literal newline after a
    backslash), not JSON (\\n). A pcall'd error() whose message contains a
    Lua stack traceback — always multi-line — broke every result that tried
    to return it as data before this was fixed (M5 finding). Not exercised by
    any real tool path today since our error() calls bubble uncaught, so this
    tests J.encode directly rather than relying on one to catch a regression."""
    result = bridge.execute(
        "return { s = \"line1\" .. string.char(10) .. \"line2\" .. string.char(9)"
        ' .. "tab" .. string.char(34) .. "quoted" .. string.char(34) }'
    )
    assert result["s"] == 'line1\nline2\ttab"quoted"'


def test_encode_handles_a_real_pcall_traceback(bridge):
    """The actual trigger case: pcall around an error, returned as data."""
    result = bridge.execute(
        "local spr = Sprite(4, 4, ColorMode.INDEXED)\n"
        "local f = spr.frames[1]\n"
        "local ok, err = pcall(function() f.frameNumber = 2 end)\n"
        "return { ok = ok, err = tostring(err) }"
    )
    assert result["ok"] is False
    assert "\n" in result["err"]  # a real traceback, not silently mangled
