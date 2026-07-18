"""Cross-language determinism test: Python mulberry32/fnv1a32 must produce
bit-for-bit identical output to client/src/prng.js. This is the load-bearing
test for the compendium's determinism requirement (§11.1) -- if this ever
goes red, client and server floor generation have diverged.
"""
import json
import pathlib
import subprocess
import sys

import pytest

from gep.prng import Mulberry32, fnv1a32, seed_from_floor, rng_for_tile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CLIENT_SRC = REPO_ROOT / "client" / "src"

SEEDS = [0, 1, 42, 0xDEADBEEF, 2**32 - 1]
N = 50


def _python_vectors():
    out = {}
    for seed in SEEDS:
        rng = Mulberry32(seed)
        out[str(seed)] = [rng.next_u32() for _ in range(N)]
    out["fnv1a32"] = {
        s: fnv1a32(s.encode("utf-8"))
        for s in ["", "hello", "tower-a:875:my-seed"]
    }
    out["seed_from_floor"] = seed_from_floor("tower-a", 875, "my-seed")
    tile_rng = rng_for_tile(out["seed_from_floor"], 3, -2)
    out["tile_rng_first5"] = [tile_rng.next_u32() for _ in range(5)]
    return out


NODE_SCRIPT = """
import { Mulberry32, fnv1a32, seedFromFloor, rngForTile } from __PRNG_PATH__;

const SEEDS = [0, 1, 42, 0xDEADBEEF, 2**32 - 1];
const N = 50;
const out = {};
for (const seed of SEEDS) {
  const rng = new Mulberry32(seed);
  const vals = [];
  for (let i = 0; i < N; i++) vals.push(rng.nextU32());
  out[String(seed)] = vals;
}
out.fnv1a32 = {};
for (const s of ["", "hello", "tower-a:875:my-seed"]) {
  out.fnv1a32[s] = fnv1a32(s);
}
out.seed_from_floor = seedFromFloor("tower-a", 875, "my-seed");
const tileRng = rngForTile(out.seed_from_floor, 3, -2);
out.tile_rng_first5 = [];
for (let i = 0; i < 5; i++) out.tile_rng_first5.push(tileRng.nextU32());
console.log(JSON.stringify(out));
"""


def _node_vectors():
    prng_url = json.dumps((CLIENT_SRC / "prng.js").resolve().as_uri())
    script = NODE_SCRIPT.replace("__PRNG_PATH__", prng_url)
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
def test_python_and_js_prng_match():
    py = _python_vectors()
    js = _node_vectors()

    for seed in SEEDS:
        assert py[str(seed)] == js[str(seed)], f"mismatch for seed {seed}"

    assert py["fnv1a32"] == js["fnv1a32"]
    assert py["seed_from_floor"] == js["seed_from_floor"]
    assert py["tile_rng_first5"] == js["tile_rng_first5"]
