"""Cross-language determinism test for floor generation itself (not just the
raw PRNG). Uses a small radius so the JS subprocess round-trip stays fast;
the algorithm is identical regardless of radius.
"""
import json
import pathlib
import subprocess

import pytest

from gep.floorgen import generate_floor

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CLIENT_SRC = REPO_ROOT / "client" / "src"

RULESET = {
    "radius": 6,
    "resource_spawn_chance": 0.15,
    "resource_weights": [["iron_ore", 3], ["copper_ore", 2]],
    "monster_spawn_count": 5,
    "monster_weights": [["cave_rat", 3], ["goblin_skirmisher", 2]],
}

CASES = [
    ("tower-a", 1, "hex-tower-v1"),
    ("tower-a", 2, "hex-tower-v1"),
    ("tower-a", 875, "hex-tower-v1"),
    ("tower-b", 1650, "hex-tower-v1"),
]


def _python_layout(tower_id, floor_number, global_seed):
    layout = generate_floor(tower_id, floor_number, global_seed, RULESET)
    return layout.to_dict()


NODE_SCRIPT = """
import { generateFloor } from __FLOORGEN_PATH__;

const ruleset = __RULESET__;
const cases = __CASES__;
const out = [];
for (const [towerId, floorNumber, globalSeed] of cases) {
  const layout = generateFloor(towerId, floorNumber, globalSeed, ruleset);
  out.push({
    tower_id: layout.tower_id,
    floor_number: layout.floor_number,
    radius: layout.radius,
    up_exit: layout.up_exit,
    down_exit: layout.down_exit,
    resource_nodes: Object.fromEntries(layout.resource_nodes),
    monster_spawns: layout.monster_spawns,
  });
}
console.log(JSON.stringify(out));
"""


def _node_layouts():
    floorgen_url = json.dumps((CLIENT_SRC / "floorgen.js").resolve().as_uri())
    script = (
        NODE_SCRIPT.replace("__FLOORGEN_PATH__", floorgen_url)
        .replace("__RULESET__", json.dumps(RULESET))
        .replace("__CASES__", json.dumps(CASES))
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.skipif(
    subprocess.run(["node", "--version"], capture_output=True).returncode != 0,
    reason="node not available",
)
def test_python_and_js_floorgen_match():
    js_layouts = _node_layouts()
    for (tower_id, floor_number, global_seed), js_layout in zip(CASES, js_layouts):
        py_layout = _python_layout(tower_id, floor_number, global_seed)
        assert py_layout == js_layout, f"mismatch for {tower_id} floor {floor_number}"


def test_floor_1_has_no_down_exit():
    layout = generate_floor("tower-a", 1, "hex-tower-v1", RULESET)
    assert layout.down_exit is None
    assert layout.up_exit is not None


def test_higher_floors_have_both_exits():
    layout = generate_floor("tower-a", 2, "hex-tower-v1", RULESET)
    assert layout.up_exit is not None
    assert layout.down_exit is not None
    assert layout.up_exit != layout.down_exit


def test_tile_count_matches_formula():
    layout = generate_floor("tower-a", 5, "hex-tower-v1", RULESET)
    r = RULESET["radius"]
    assert len(layout.tiles) == 3 * r * r + 3 * r + 1


def test_deterministic_repeat():
    a = generate_floor("tower-a", 42, "hex-tower-v1", RULESET).to_dict()
    b = generate_floor("tower-a", 42, "hex-tower-v1", RULESET).to_dict()
    assert a == b
