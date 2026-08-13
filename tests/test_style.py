import numpy as np
import pytest

from aseprite_mcp.errors import ToolError
from aseprite_mcp.style import StyleBible, auto_ramps, available_projects, outline_candidates

# a deliberate two-family palette: index 0 transparent, three reds, three blues
PAL = ["#000000", "#3a0a14", "#a01838", "#d43a5a", "#0c2a4a", "#1a5a9a", "#4a9ade"]


def test_auto_ramps_groups_by_hue_and_orders_dark_to_light():
    ramps = auto_ramps(PAL)
    assert set(ramps) == {"red", "blue"}
    assert ramps["red"] == [1, 2, 3]
    assert ramps["blue"] == [4, 5, 6]


def test_auto_ramps_excludes_index_0_because_it_is_transparency():
    for idxs in auto_ramps(PAL).values():
        assert 0 not in idxs


def test_auto_ramps_drops_single_colour_families():
    # one lone green cannot form a ramp
    ramps = auto_ramps(["#000000", "#a01838", "#d43a5a", "#2f7a26"])
    assert "green" not in ramps


def test_outline_candidates_are_the_darkest_non_transparent_entries():
    assert outline_candidates(PAL, n=2) == [1, 4]


def test_roles_round_trip_as_ints_not_json_string_keys(tmp_path):
    """JSON object keys are always strings. The plan's A1 sketch loads with
    cls(**json.loads(...)), which silently yields {"8": "skin"} and breaks
    every lookup by palette index."""
    bible = StyleBible.create("proj", PAL)
    bible.roles = {3: "skin_base", 6: "metal_light"}
    bible.save(tmp_path)

    loaded = StyleBible.load(tmp_path, "proj")
    assert loaded.roles == {3: "skin_base", 6: "metal_light"}
    assert all(isinstance(k, int) for k in loaded.roles)


def test_round_trips_every_field(tmp_path):
    bible = StyleBible.create("proj", PAL)
    bible.save(tmp_path)
    assert StyleBible.load(tmp_path, "proj") == bible


def test_load_missing_project_names_the_available_ones(tmp_path):
    StyleBible.create("dungeon", PAL).save(tmp_path)
    with pytest.raises(ToolError) as e:
        StyleBible.load(tmp_path, "forest")
    assert e.value.code == "style_project_not_found"
    assert "dungeon" in str(e.value)


def test_available_projects_ignores_dirs_without_a_bible(tmp_path):
    StyleBible.create("real", PAL).save(tmp_path)
    (tmp_path / "empty").mkdir()
    assert available_projects(tmp_path) == ["real"]


def test_validate_rejects_out_of_range_indices_and_reports_all_of_them():
    bible = StyleBible.create("proj", PAL)
    bible.ramps = {"red": [1, 99]}
    bible.roles = {50: "nope"}
    with pytest.raises(ToolError) as e:
        bible.validate()
    assert e.value.code == "style_index_out_of_range"
    assert "ramp 'red'" in str(e.value) and "roles" in str(e.value)


def test_apply_rejects_unknown_field_instead_of_dropping_it():
    bible = StyleBible.create("proj", PAL)
    with pytest.raises(ToolError) as e:
        bible.apply({"shading_stepz": 4})
    assert e.value.code == "unknown_style_field"


def test_replacing_palette_regenerates_ramps_so_they_cannot_dangle():
    bible = StyleBible.create("proj", PAL)
    # PAL already starts with a black at index 0; reserving shifts everything up
    assert bible.ramps["red"] == [2, 3, 4]
    changed = bible.apply({"palette": ["#000000", "#0c2a4a", "#1a5a9a", "#4a9ade"]})
    assert "ramps" in changed
    assert set(bible.ramps) == {"blue"}
    bible.validate()  # no index now points past the shorter palette


def test_explicit_ramps_survive_a_palette_change():
    bible = StyleBible.create("proj", PAL)
    bible.apply({"palette": PAL[:4], "ramps": {"custom": [1, 2]}})
    assert bible.ramps == {"custom": [1, 2]}


def test_palette_is_stored_lowercase_whatever_case_it_arrives_in():
    """Bundled presets ship uppercase hex, Aseprite reads colors back lowercase,
    and set_palette's off-style check compares them as strings. Without
    canonical casing that warning fires on every correctly-on-palette sprite."""
    from aseprite_mcp.color import TRANSPARENT_PLACEHOLDER

    bible = StyleBible.create("proj", ["#000000", "#3A0A14", "#A01838", "#D43A5A"])
    assert bible.palette == [TRANSPARENT_PLACEHOLDER, "#000000", "#3a0a14", "#a01838", "#d43a5a"]
    bible.apply({"palette": ["#000000", "#0C2A4A", "#1A5A9A"]})
    assert bible.palette == [TRANSPARENT_PLACEHOLDER, "#000000", "#0c2a4a", "#1a5a9a"]


def test_project_palette_reserves_index_0_so_no_colour_is_silently_lost():
    """Aseprite never draws entry 0. Without reserving, a project created from a
    16-colour preset is a 15-colour project and nothing says so -- and the entry
    it loses is index 0, which for every bundled preset is the darkest colour,
    the one pixel-art outlines want most."""
    from aseprite_mcp.color import TRANSPARENT_PLACEHOLDER
    from aseprite_mcp.palettes import load_preset

    preset = load_preset("pico8")["colors"]
    bible = StyleBible.create("proj", preset)

    assert bible.palette[0] == TRANSPARENT_PLACEHOLDER
    assert len(bible.palette) - 1 == len(preset), "a colour went missing"
    assert "#000000" in bible.palette[1:], "the darkest colour must be drawable"


def test_reserving_survives_a_palette_update_without_stacking():
    from aseprite_mcp.color import TRANSPARENT_PLACEHOLDER
    from aseprite_mcp.palettes import load_preset

    bible = StyleBible.create("proj", load_preset("pico8")["colors"])
    bible.apply({"palette": load_preset("db16")["colors"]})
    assert bible.palette.count(TRANSPARENT_PLACEHOLDER) == 1
    assert bible.palette[0] == TRANSPARENT_PLACEHOLDER
