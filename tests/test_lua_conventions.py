"""CLAUDE.md's Lua rules, asserted over the source rather than trusted.

Rule 7 (every mutation wraps in `J.tx()`) had drifted on 18 sites — the whole of
structure.py's layers/frames/tags, plus two in drawing.py and one in
reference.py. drawing.py used `J.tx` correctly in its pixel paths, so the rule
was understood; it simply was never applied to the structural tools, and nothing
noticed for the project's whole life.

Every CLAUDE.md rule that has stayed enforced in this codebase has a test
asserting it. Every rule that quietly went unimplemented did not. This is that
test for the Lua conventions.
"""

import re
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "aseprite_mcp"


def _execute_chunks() -> list[tuple[Path, int, str]]:
    """Every `bridge.execute(...)` call's argument text, with its location."""
    out: list[tuple[Path, int, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        src = path.read_text()
        for m in re.finditer(r"bridge\.execute\(", src):
            i, depth = m.end(), 1
            while i < len(src) and depth:
                depth += (src[i] == "(") - (src[i] == ")")
                i += 1
            out.append((path, src[: m.start()].count("\n") + 1, src[m.end() : i]))
    return out


def test_there_are_chunks_to_check():
    """A parser that silently matches nothing would pass everything below."""
    chunks = _execute_chunks()
    assert len(chunks) > 20, f"only found {len(chunks)} execute calls — parser is broken"
    assert any("J.save" in c for _, _, c in chunks)


def test_every_mutating_chunk_wraps_in_a_transaction():
    """CLAUDE.md #7. A chunk that saves has mutated; without J.tx a failure
    partway leaves the sprite changed AND written, with no rollback."""
    offenders = [
        f"{p.relative_to(SRC)}:{ln}"
        for p, ln, c in _execute_chunks()
        if "J.save" in c and "J.tx" not in c
    ]
    assert not offenders, "mutations without J.tx():\n  " + "\n  ".join(offenders)


def test_no_chunk_wraps_sprite_resolution_inside_the_transaction():
    """The other half of #7, and the expensive half to relearn: app.transaction()
    needs a document already active when it is CALLED, not merely by the time its
    callback runs. Resolving the sprite inside J.tx crashes the whole Aseprite
    process — `exited 255`, not a catchable Lua error (found in M9)."""
    offenders = []
    for p, ln, c in _execute_chunks():
        tx = c.find("J.tx")
        if tx == -1:
            continue
        # J.sprite must be resolved before the transaction opens
        for m in re.finditer(r"J\.sprite\(", c):
            if m.start() > tx:
                offenders.append(f"{p.relative_to(SRC)}:{ln}")
                break
    assert not offenders, "J.sprite() resolved inside J.tx():\n  " + "\n  ".join(offenders)


def test_cel_images_are_cloned_before_mutation():
    """CLAUDE.md #8: in-place edits to cel.image bleed into linked cels."""
    offenders = []
    for p, ln, c in _execute_chunks():
        if re.search(r"\.image:drawPixel|__cel\.image:draw", c) and "clone()" not in c:
            offenders.append(f"{p.relative_to(SRC)}:{ln}")
    assert not offenders, "cel.image mutated without clone():\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize("shadowed", ["os.execute", "os.remove", "io.popen"])
def test_prelude_shadows_the_shell_escape_surface(shadowed):
    """Security section: run_lua executes arbitrary code by design, so these are
    shadowed for every command including its own."""
    prelude = (SRC / "bridge" / "lua" / "prelude.lua").read_text()
    assert f"{shadowed} = function()" in prelude


# `run_lua` interpolates its `script` argument raw, by design: rule 17 keeps the
# escape hatch ("a missing wrapper must never block a user") and escaping a Lua
# payload would defeat the entire tool. It is bounded by the sandbox instead —
# shadowed shell surface, a timeout, a transaction, and every script logged.
_RAW_BY_DESIGN = {("tools/escape.py", "script")}
_USER_VALUES = {"path", "name", "new_name", "layer", "sprite"}


def test_no_user_value_is_f_strung_into_lua_unescaped():
    """CLAUDE.md #6: type-validate scalars, escape strings with lua_str()."""
    offenders = []
    for p, ln, c in _execute_chunks():
        rel = str(p.relative_to(SRC))
        for var in re.findall(r"\{(\w+)\}", c):
            if (rel, var) in _RAW_BY_DESIGN:
                continue
            if var in _USER_VALUES and f"lua_str({var})" not in c:
                offenders.append(f"{rel}:{ln} interpolates {{{var}}} raw")
    assert not offenders, "unescaped user values in Lua:\n  " + "\n  ".join(offenders)


def test_the_raw_by_design_exemption_has_not_gone_stale():
    """If run_lua is renamed or removed, the exemption must follow it, or it
    silently exempts nothing while a real interpolation goes unchecked."""
    for rel, _ in _RAW_BY_DESIGN:
        assert (SRC / rel).exists(), f"exemption points at a missing file: {rel}"
