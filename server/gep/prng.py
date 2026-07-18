"""Deterministic PRNG shared bit-for-bit with client/src/prng.js.

See shared/prng_spec.md for the algorithm spec. Do not modify this file
without mirroring the change in prng.js and re-running the golden test.
"""

MASK32 = 0xFFFFFFFF


def imul(x: int, y: int) -> int:
    return (x * y) & MASK32


def fnv1a32(data: bytes) -> int:
    h = 0x811C9DC5
    for byte in data:
        h ^= byte
        h = imul(h, 0x01000193)
    return h


class Mulberry32:
    __slots__ = ("_state",)

    def __init__(self, seed: int):
        self._state = seed & MASK32

    def next_u32(self) -> int:
        a = (self._state + 0x6D2B79F5) & MASK32
        self._state = a
        t = a
        t = imul(t ^ (t >> 15), t | 1)
        t = (t + imul(t ^ (t >> 7), t | 61)) & MASK32
        t = (t ^ (t >> 14)) & MASK32
        return t

    def next_float(self) -> float:
        return self.next_u32() / 4294967296.0


def seed_from_floor(tower_id: str, floor_number: int, global_seed: str) -> int:
    key = f"{tower_id}:{floor_number}:{global_seed}".encode("utf-8")
    return fnv1a32(key)


def rng_for_tile(floor_seed: int, q: int, r: int) -> Mulberry32:
    key = f"{floor_seed}:{q}:{r}".encode("utf-8")
    return Mulberry32(fnv1a32(key))
