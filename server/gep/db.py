"""SQLite-backed persistence: player progress, accounts, and sessions.

Password storage uses PBKDF2-SHA256 (stdlib, no extra dependencies).
This is intentionally swappable for OAuth/Google auth later -- the
session-token contract is the same regardless of the auth back-end.
"""
import binascii
import hashlib
import json
import os
import pathlib
import sqlite3
import uuid

DB_PATH = pathlib.Path(__file__).resolve().parents[2] / "players.db"

_DDL_PLAYERS = """
CREATE TABLE IF NOT EXISTS players (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    combat_xp         TEXT NOT NULL DEFAULT '{}',
    non_combat_xp     TEXT NOT NULL DEFAULT '{}',
    combat_levels     TEXT NOT NULL DEFAULT '{}',
    non_combat_levels TEXT NOT NULL DEFAULT '{}',
    inventory         TEXT NOT NULL DEFAULT '{}',
    equipment         TEXT NOT NULL DEFAULT '{}'
)
"""

_DDL_ACCOUNTS = """
CREATE TABLE IF NOT EXISTS accounts (
    username    TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    player_id   TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_DDL_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    player_id   TEXT NOT NULL,
    username    TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH))
    c.execute(_DDL_PLAYERS)
    c.execute(_DDL_ACCOUNTS)
    c.execute(_DDL_SESSIONS)
    _migrate(c)
    c.commit()
    return c


def _migrate(c: sqlite3.Connection) -> None:
    existing = {row[1] for row in c.execute("PRAGMA table_info(players)")}
    for col, default in [("inventory", "'{}'"), ("equipment", "'{}'")]:
        if col not in existing:
            c.execute(f"ALTER TABLE players ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}")
    # Vitality is nullable on purpose: NULL means "no saved value", which is
    # how a pre-existing row (or a brand new character) asks to start at full
    # health rather than at some number chosen by a migration default.
    for col in ("hp", "mana"):
        if col not in existing:
            c.execute(f"ALTER TABLE players ADD COLUMN {col} REAL")
    # Respawn anchor: the last town the player visited. NULL until they set foot
    # in one, which the caller reads as "no town yet -> start on floor 1".
    if "spawn_floor" not in existing:
        c.execute("ALTER TABLE players ADD COLUMN spawn_floor INTEGER")
    if "spawn_tile" not in existing:
        c.execute("ALTER TABLE players ADD COLUMN spawn_tile TEXT")


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
    return binascii.hexlify(salt).decode() + ":" + binascii.hexlify(dk).decode()


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split(":")
        salt = binascii.unhexlify(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
        return binascii.hexlify(dk).decode() == dk_hex
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def register(username: str, password: str) -> tuple[str | None, str | None]:
    """Returns (player_id, None) on success or (None, error_reason)."""
    if not username or not password:
        return None, "username and password required"
    if len(username) < 3:
        return None, "username must be at least 3 characters"
    if len(password) < 6:
        return None, "password must be at least 6 characters"

    player_id = str(uuid.uuid4())
    pw_hash = _hash_password(password)
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO accounts (username, password_hash, player_id) VALUES (?, ?, ?)",
                (username.lower(), pw_hash, player_id),
            )
            c.execute(
                "INSERT INTO players (id, name) VALUES (?, ?)",
                (player_id, username),
            )
        return player_id, None
    except sqlite3.IntegrityError:
        return None, "username already taken"


def login(username: str, password: str) -> tuple[str | None, str | None]:
    """Returns (player_id, None) on success or (None, error_reason)."""
    with _conn() as c:
        row = c.execute(
            "SELECT player_id, password_hash FROM accounts WHERE username = ?",
            (username.lower(),),
        ).fetchone()
    if row is None:
        return None, "invalid username or password"
    player_id, pw_hash = row
    if not _verify_password(password, pw_hash):
        return None, "invalid username or password"
    return player_id, None


def create_session(player_id: str, username: str) -> str:
    token = str(uuid.uuid4())
    with _conn() as c:
        c.execute(
            "INSERT INTO sessions (token, player_id, username) VALUES (?, ?, ?)",
            (token, player_id, username),
        )
    return token


def validate_session(token: str) -> tuple[str, str] | None:
    """Returns (player_id, username) or None if invalid."""
    if not token:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT player_id, username FROM sessions WHERE token = ?",
            (token,),
        ).fetchone()
    return (row[0], row[1]) if row else None


# ---------------------------------------------------------------------------
# Player persistence
# ---------------------------------------------------------------------------

def load_player(player_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT name, combat_xp, non_combat_xp, combat_levels, non_combat_levels, "
            "inventory, equipment, hp, mana, spawn_floor, spawn_tile FROM players WHERE id = ?",
            (player_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "name": row[0],
        "combat_xp": json.loads(row[1]),
        "non_combat_xp": json.loads(row[2]),
        "combat_levels": json.loads(row[3]),
        "non_combat_levels": json.loads(row[4]),
        "inventory": json.loads(row[5]),
        "equipment": json.loads(row[6]),
        # None when the character predates vitality persistence, or is new.
        "hp": row[7],
        "mana": row[8],
        # None until the player has visited a town.
        "spawn_floor": row[9],
        "spawn_tile": json.loads(row[10]) if row[10] else None,
    }


def save_player(player) -> None:
    inv = {str(k): v for k, v in player.inventory.items() if v is not None}
    equip = player.equipment.to_dict()
    with _conn() as c:
        c.execute(
            """INSERT INTO players
                   (id, name, combat_xp, non_combat_xp, combat_levels,
                    non_combat_levels, inventory, equipment, hp, mana,
                    spawn_floor, spawn_tile)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   name              = excluded.name,
                   combat_xp         = excluded.combat_xp,
                   non_combat_xp     = excluded.non_combat_xp,
                   combat_levels     = excluded.combat_levels,
                   non_combat_levels = excluded.non_combat_levels,
                   inventory         = excluded.inventory,
                   equipment         = excluded.equipment,
                   hp                = excluded.hp,
                   mana              = excluded.mana,
                   spawn_floor       = excluded.spawn_floor,
                   spawn_tile        = excluded.spawn_tile""",
            (
                player.id,
                player.name,
                json.dumps(player.skills.combat_xp),
                json.dumps(player.skills.non_combat_xp),
                json.dumps(player.skills.combat),
                json.dumps(player.skills.non_combat),
                json.dumps(inv),
                json.dumps(equip),
                player.hp,
                player.mana,
                player.spawn_floor,
                json.dumps(list(player.spawn_tile)),
            ),
        )
