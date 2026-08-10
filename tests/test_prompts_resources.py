import pytest
from mcp.client import Client

from aseprite_mcp.server import mcp

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path, monkeypatch, aseprite_exe):
    monkeypatch.setenv("ASEPRITE_PATH", str(aseprite_exe))
    monkeypatch.setenv("ASEPRITE_MCP_WORKSPACE", str(tmp_path))
    return tmp_path


async def test_all_five_prompts_are_registered(workspace):
    async with Client(mcp) as c:
        prompts = await c.list_prompts()
        names = {p.name for p in prompts.prompts}
        assert names == {
            "sprite-character", "sprite-tileset", "animate-walkcycle",
            "from-reference", "palette-explore",
        }


async def test_sprite_character_prompt_interpolates_args(workspace):
    async with Client(mcp) as c:
        got = await c.get_prompt(
            "sprite-character", {"size": "32", "description": "a knight", "palette": "pico8"}
        )
        text = got.messages[0].content.text
        assert "32x32" in text
        assert "a knight" in text
        assert "palette=pico8" in text
        assert "silhouette" in text.lower()


async def test_three_resources_are_registered(workspace):
    async with Client(mcp) as c:
        resources = await c.list_resources()
        uris = {str(r.uri) for r in resources.resources}
        assert uris == {
            "aseprite://guide/pixel-art", "aseprite://guide/lua-api", "aseprite://palettes",
        }


async def test_pixel_art_guide_covers_the_named_topics(workspace):
    async with Client(mcp) as c:
        out = await c.read_resource("aseprite://guide/pixel-art")
        text = out.contents[0].text
        for topic in ["silhouette", "pillow shading", "orphan", "outline"]:
            assert topic in text.lower()


async def test_lua_api_guide_documents_real_gotchas_found_while_building(workspace):
    async with Client(mcp) as c:
        out = await c.read_resource("aseprite://guide/lua-api")
        text = out.contents[0].text
        assert "isEditable" in text  # the isLocked gotcha from M6
        assert "Color(\"#ff0000\")" in text  # the hex-string gotcha from M4


async def test_palettes_resource_lists_bundled_presets_with_real_hex(workspace):
    async with Client(mcp) as c:
        out = await c.read_resource("aseprite://palettes")
        text = out.contents[0].text
        assert "#1D2B53" in text or "#1d2b53" in text.lower()  # PICO-8 index 1
