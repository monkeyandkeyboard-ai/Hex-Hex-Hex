"""The generation pipeline (gep/pipeline.py) is composed from config: each
archetype names an ordered list of stage ids. These tests cover the two things
that composition must never be able to break -- the navigability guarantee,
and failing loudly on a stack that doesn't make sense.
"""
import pathlib

import pytest

from gep.config_loader import ConfigError, ConfigStore
from gep.floorgen import generate_floor
from gep.pipeline import STAGE_REGISTRY, TERMINAL_STAGES

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"
cfg = ConfigStore(CONFIG_DIR)


def _archetypes_with(pipeline, name="rocky_depths"):
    """A copy of the real archetype config with one archetype's stack replaced."""
    import copy
    archetypes = copy.deepcopy(cfg.floor_archetypes)
    archetypes["archetypes"][name]["pipeline"] = pipeline
    archetypes["default_archetype"] = name
    archetypes["overrides"] = []
    return archetypes


def _gen(archetypes, floor_number=6):
    return generate_floor(
        "tower-a", floor_number, "test-pipeline",
        cfg.floor_ruleset, archetypes, cfg.biomes, prefabs=cfg.prefabs,
    )


def test_every_configured_stage_is_registered():
    for name, params in cfg.floor_archetypes["archetypes"].items():
        for stage_id in params["pipeline"]:
            assert stage_id in STAGE_REGISTRY, f"{name} names unregistered stage {stage_id}"


def test_terminal_stages_run_even_when_config_omits_everything():
    # The whole point of the terminal pair: a minimal stack still yields a
    # floor with carved roads and validated reachability.
    layout = _gen(_archetypes_with(["noise_fields", "macro_layout"]))
    assert layout.roads, "roads must be carved regardless of configured stages"


def test_config_cannot_schedule_terminal_stages():
    for stage_id in TERMINAL_STAGES:
        with pytest.raises(ConfigError, match="always run last"):
            ConfigStore._validate_pipeline("x", {"pipeline": ["noise_fields", stage_id]})


def test_unknown_stage_is_rejected_at_load():
    with pytest.raises(ConfigError, match="unknown pipeline stage"):
        ConfigStore._validate_pipeline("x", {"pipeline": ["noise_fields", "carve_lava"]})


def test_missing_pipeline_is_rejected_at_load():
    with pytest.raises(ConfigError, match="'pipeline' is required"):
        ConfigStore._validate_pipeline("x", {})


def test_prefabs_declared_but_stage_omitted_is_rejected():
    with pytest.raises(ConfigError, match="omits the 'prefabs' stage"):
        ConfigStore._validate_pipeline(
            "x", {"pipeline": ["noise_fields"], "prefabs": ["monster_encampment"]}
        )


def test_generation_params_have_no_silent_defaults():
    # Each of these used to fall back to a hardcoded number in Python. An
    # omission must now fail at load rather than generate a floor from a
    # value nobody chose.
    base = {
        "elevation": {"octaves": 4, "scale": 0.05},
        "roughness": {"octaves": 4, "scale": 0.24},
        "layout": {"mode": "cluster", "region_count": 6},
    }

    def without(path):
        import copy
        params = copy.deepcopy(base)
        target = params
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
        return params

    for path in (
        ("elevation", "octaves"), ("elevation", "scale"),
        ("roughness", "octaves"), ("roughness", "scale"),
        ("layout", "region_count"),
    ):
        with pytest.raises(ConfigError, match="required|needs"):
            ConfigStore._validate_generation_params("x", without(path))


def test_extremity_block_requires_both_thresholds():
    params = {
        "elevation": {"octaves": 4, "scale": 0.05},
        "roughness": {"octaves": 4, "scale": 0.24},
        "layout": {"mode": "elevation", "extremity": {"biome": "hazard"}},
    }
    with pytest.raises(ConfigError, match="extremity"):
        ConfigStore._validate_generation_params("x", params)


def test_dropping_a_stage_changes_the_floor():
    # Composition has to actually do something. rocky_depths is the archetype
    # where template_constraints has real work: its cluster layout can spread
    # a hazard cell inside hazard's own min_radius_pct, and the stage is what
    # rewrites those tiles back to the fallback biome.
    full = _gen(_archetypes_with([
        "noise_fields", "macro_layout", "template_constraints",
        "flatten_elevation", "biome_adjacency", "prefabs",
    ]))
    without = _gen(_archetypes_with([
        "noise_fields", "macro_layout", "flatten_elevation", "prefabs",
    ]))
    assert full.regions != without.regions


def test_reordered_stack_is_still_deterministic():
    stack = ["noise_fields", "macro_layout", "biome_adjacency", "prefabs"]
    a = _gen(_archetypes_with(stack))
    b = _gen(_archetypes_with(stack))
    assert a.regions == b.regions
    assert a.roads == b.roads
    assert [p.anchor for p in a.prefabs] == [p.anchor for p in b.prefabs]
