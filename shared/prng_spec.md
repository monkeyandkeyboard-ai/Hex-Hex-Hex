# Shared deterministic PRNG spec

Used by floor generation (server: Python, client: JS) so both sides produce
bit-for-bit identical output from the same inputs. Do not use either
language's built-in RNG for anything that must match across languages.

## Algorithm: mulberry32

32-bit state, 32-bit unsigned arithmetic throughout (every intermediate value
is masked to `0xFFFFFFFF` after each operation — this removes any reliance on
JS `ToInt32` coercion semantics or Python's arbitrary-precision ints, so the
two implementations can be verified line-by-line against each other).

Given 32-bit unsigned seed `a`, each call to `next_u32()`:

```
a = (a + 0x6D2B79F5) & 0xFFFFFFFF
t = a
t = imul(t ^ (t >> 15), t | 1) & 0xFFFFFFFF
t = (t + imul(t ^ (t >> 7), t | 61)) & 0xFFFFFFFF
t = (t ^ (t >> 14)) & 0xFFFFFFFF
return t   # this is next_u32(); a is the persisted generator state
```

`imul(x, y)` = the low 32 bits of the full integer product `x * y`, i.e.
`(x * y) & 0xFFFFFFFF`. In JS this is exactly `Math.imul(x, y) >>> 0`; in
Python it is `(x * y) & 0xFFFFFFFF`. All shifts (`>>`) are logical
(zero-fill) shifts on the unsigned 32-bit value — never arithmetic/sign-extending.

`next_float()` = `next_u32() / 4294967296` → a float in `[0, 1)`.

## Seeding from named inputs (floor generation)

Floor generation never seeds mulberry32 directly from small integers (poor
avalanche for near-identical seeds like adjacent floor numbers). Instead:

1. Build the seed string: `f"{tower_id}:{floor_number}:{global_seed}"`
   (all three inputs are part of the compendium's stated generation inputs,
   §4.1: tower ID, floor number, seed, generation ruleset).
2. Hash it with 32-bit FNV-1a to get a 32-bit unsigned seed:

```
def fnv1a32(s: bytes) -> u32:
    h = 0x811c9dc5
    for byte in s:
        h = h ^ byte
        h = imul(h, 0x01000193) & 0xFFFFFFFF
    return h
```

   (encode the seed string as UTF-8 bytes before hashing.)
3. Feed that as the initial `a` to mulberry32.

Per-tile rolls (resource/feature presence) additionally mix in the tile's
axial coordinates before hashing, so tile order never matters:
`fnv1a32(f"{floor_seed_u32}:{q}:{r}")` → fresh mulberry32 instance per tile,
first draw only. This makes generation embarrassingly parallel and immune to
iteration-order bugs between the two language implementations.

## Cross-language verification

`server/tests/test_prng_golden.py` generates N values from a fixed seed in
Python, shells out to Node to generate the same N values from `client/src/prng.js`,
and asserts the sequences are identical. This test must stay green — it is
the thing standing between "looks deterministic" and "is deterministic."
