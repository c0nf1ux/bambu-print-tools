from pathlib import Path

import pytest

from bambu_print_tools.slicer import find_preset, flatten_preset, resolve

FIXTURES = str(Path(__file__).parent / "fixtures" / "profiles")


def test_find_preset_locates_file_under_kind_dir():
    path = find_preset(FIXTURES, "process", "Base Process")
    assert path.endswith("Base Process.json")


def test_find_preset_raises_when_missing():
    with pytest.raises(FileNotFoundError):
        find_preset(FIXTURES, "process", "Does Not Exist")


def test_resolve_merges_parent_into_child():
    """This is the exact bug class that caused a real failed print
    (bottle_row #1, 2026-09-10): the CLI doesn't follow `inherits` on its
    own, so a child preset that only overrides one key must still end up
    with the parent's other keys when flattened by hand."""
    merged = resolve(FIXTURES, "process", "Child Process")
    assert merged["layer_height"] == "0.2"       # inherited from Base
    assert merged["curr_bed_type"] == "Cool Plate"  # inherited from Base
    assert merged["brim_type"] == "outer_only"    # child's own override


def test_flatten_preset_drops_meta_keys_and_marks_as_user_preset():
    flat = flatten_preset(FIXTURES, "process", "Child Process")
    assert "inherits" not in flat
    assert "setting_id" not in flat
    assert flat["from"] == "User"
    assert flat["type"] == "process"
    assert flat["name"] == "Child Process (flattened)"


def test_flatten_preset_applies_project_overrides_last():
    flat = flatten_preset(
        FIXTURES, "process", "Child Process",
        overrides={"curr_bed_type": "Textured PEI Plate"},
    )
    assert flat["curr_bed_type"] == "Textured PEI Plate"
    assert flat["brim_type"] == "outer_only"  # untouched keys survive


def test_flatten_preset_refuses_machine_kind():
    with pytest.raises(ValueError):
        flatten_preset(FIXTURES, "machine", "whatever")


def test_resolve_detects_inherits_cycle():
    with pytest.raises(RuntimeError):
        resolve(FIXTURES, "process", "Child Process", _seen={"Child Process"})
