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
