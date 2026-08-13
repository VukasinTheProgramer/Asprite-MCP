"""Properties every registered tool must hold, checked across the whole surface
rather than tool by tool.

`detect_grid` shipped with structured_output=False while returning a TypedDict,
so it handed back structured_content=None to every caller and B3's acceptance
test failed on any machine with an Aseprite binary. Eleven tests asserted on
structured_content; none of them covered that tool. A property checked over the
registry does not have that blind spot.

These need no binary: mcp.list_tools() reads the registry, it does not open a
session, so there is no lifespan and no Aseprite resolution (CLAUDE.md's
integration-skip rule is about Client(mcp), which is a different thing).
"""

import pytest

from aseprite_mcp.server import mcp

pytestmark = pytest.mark.asyncio

# Tools that return prose or images rather than data, so they legitimately have
# no output schema. Every other tool must have one.
TEXT_OR_IMAGE_ONLY = {
    "create_sprite", "draw_grid", "draw_shape", "fill", "set_palette", "get_ramp",
    "layers", "frames", "tags", "export", "import_reference", "run_lua",
    "undo", "redo", "cleanup", "conform_image", "style",
}


async def test_every_data_returning_tool_publishes_an_output_schema():
    for tool in await mcp.list_tools():
        if tool.name in TEXT_OR_IMAGE_ONLY:
            continue
        assert tool.output_schema, (
            f"{tool.name} returns data but publishes no output schema, so "
            "structured_content will be None for every caller. Drop "
            "structured_output=False (CLAUDE.md #26/#27)."
        )


async def test_no_tool_leaks_an_injected_dependency_into_its_input_schema():
    """Resolve()-wrapped params are server-side and must stay invisible to the
    model (CLAUDE.md #28). One leaking would make the model try to supply it."""
    for tool in await mcp.list_tools():
        props = set(tool.input_schema.get("properties", {}))
        assert not props & {"bridge", "session", "ctx", "context"}, (
            f"{tool.name} exposes an injected dependency: {sorted(props & {'bridge', 'session', 'ctx', 'context'})}"
        )


async def test_every_tool_has_a_model_facing_docstring():
    """Docstrings are prompt surface, not commentary (CLAUDE.md #18)."""
    for tool in await mcp.list_tools():
        assert tool.description and len(tool.description) > 80, (
            f"{tool.name} has no usable description for the model"
        )


async def test_the_known_text_only_list_has_not_gone_stale():
    """If a tool is renamed or removed, the exemption list above must follow it,
    or it silently starts exempting nothing while a real tool goes unchecked."""
    names = {t.name for t in await mcp.list_tools()}
    assert TEXT_OR_IMAGE_ONLY <= names, f"stale exemptions: {sorted(TEXT_OR_IMAGE_ONLY - names)}"


# --- prompts ------------------------------------------------------------------
# The v1 prompts named zero of the Phase A/B tools, so `style`, `conform_image`
# and `cleanup` shipped and nothing pointed the model at them. Prompts are the
# discovery surface; drift there makes features invisible rather than broken,
# which is why no test caught it.

async def test_prompts_point_at_the_tools_that_exist_now():
    bodies = await _all_prompt_text()
    for tool in ("style(", "cleanup(", "conform_image(", "draw_grid"):
        assert any(tool in b for b in bodies.values()), f"no prompt mentions {tool}"


async def test_every_prompt_anchors_on_the_style_project():
    """A prompt that skips the style anchor re-derives a palette per sprite,
    which is the drift Phase A exists to stop."""
    bodies = await _all_prompt_text()
    for name, body in bodies.items():
        assert "style(" in body, f"{name} never mentions the style tool"


async def test_no_prompt_uses_the_removed_create_sprite_signature():
    """v1 prompts called create_sprite(size, size, indexed, palette=...).
    create_sprite has never taken a palette, and now takes asset_type."""
    bodies = await _all_prompt_text()
    for name, body in bodies.items():
        assert "indexed, palette=" not in body, f"{name} uses the v1 call shape"


async def _all_prompt_text() -> dict[str, str]:
    """Render every prompt with placeholder arguments and return its text."""
    out: dict[str, str] = {}
    for p in await mcp.list_prompts():
        args = {}
        for a in p.arguments or []:
            args[a.name] = "2" if a.name in ("size", "tile_size", "frames") else "x"
        result = await mcp.get_prompt(p.name, args)
        out[p.name] = "\n".join(
            m.content.text for m in result.messages if getattr(m.content, "type", None) == "text"
        )
    return out


# --- docs ---------------------------------------------------------------------

async def test_readme_documents_every_registered_tool_and_no_ghosts():
    """The README listed only the v1 tools: `style`, `conform_image`, `cleanup`
    and `detect_grid` shipped undocumented. Anyone installing got docs for a
    strictly smaller product than they had."""
    import re
    from pathlib import Path

    readme = (Path(__file__).parent.parent / "README.md").read_text()
    table = re.findall(r"^\| `([a-z_]+)`", readme, re.M)
    documented = set(table)
    registered = {t.name for t in await mcp.list_tools()}
    # undo/redo share one row, and get_ramp/get_region_as_grid are listed inline
    registered -= {"redo"}

    missing = registered - documented
    assert not missing, f"registered but undocumented: {sorted(missing)}"

    ghosts = documented - registered - {"undo", "style-setup"}
    assert not ghosts, f"documented but not registered: {sorted(ghosts)}"


async def test_readme_documents_every_prompt():
    import re
    from pathlib import Path

    readme = (Path(__file__).parent.parent / "README.md").read_text()
    documented = set(re.findall(r"`([a-z-]+)`", readme))
    for p in await mcp.list_prompts():
        assert p.name in documented, f"prompt {p.name} is undocumented"
