# Hex Tower MUD

Browser-playable, tick-based MMO. Full design spec: `MUD_Compendium.md` (kept
alongside this README — treat it as the source of truth for every rule,
formula, and open design question).

## Layout

- `server/gep/` — Game Engine Process (Python). Owns the tick loop, floor
  generation, combat resolution, and all authoritative game state.
- `server/config/` — JSON content/tuning config, loaded at worker startup.
  Every monster, item, and tunable number lives here, never in code.
- `server/tests/` — pytest suite, including the cross-language PRNG golden
  test (`test_prng_golden.py`).
- `client/src/` — plain JS web client. Renders authoritative server state,
  sends intent, predicts nothing except cosmetic tweening.
- `shared/prng_spec.md` — the one PRNG algorithm both server and client
  implement identically, so floor generation matches bit-for-bit.

## Running (V1 dev loop)

```
# server
cd server
python -m gep.server

# client (separate terminal)
cd client
npm run dev
```

## Testing

```
cd server
python -m pytest
```

## Status

V1 build target per compendium §25: one tower, a handful of floors, one GEP
worker, hex movement, combat vs 1-2 monster templates, 1-2 gatherable
resources, all-JSON config, dumb web client. Everything else in the
compendium (trade venues, factions, Precision Grade, monetization, analytics)
is deliberately deferred until this loop is proven fun.
