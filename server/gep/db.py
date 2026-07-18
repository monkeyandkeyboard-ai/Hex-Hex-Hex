"""SQLite-backed player persistence.

Stores skill levels and XP so progress survives server restarts.
Position is always reset to the spawn tile on connect -- floor state
is ephemeral; only earned progress is durable.
"""
import json
import pathlib
import sqlite3

DB_PATH = pathlib.Path(__file__).resolve().parents[2] / "players.db"

_DDL = """
CREATE TABLE IF NOT EXISTS players (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    combat_xp       TEXT NOT NULL DEFAULT '{}',
    non_combat_xp   TEXT NOT NULL DEFAULT '{}',
    combat_levels   TEXT NOT NULL DEFAULT '{}',
    non_combat_levels TEXT NOT NULL DEFAULT '{}'
)
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH))
    c.execute(_DDL)
    c.commit()
    return c


def load_player(player_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT name, combat_xp, non_combat_xp, combat_levels, non_combat_levels "
            "FROM players WHERE id = ?",
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
    }


def save_player(player) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO players
                   (id, name, combat_xp, non_combat_xp, combat_levels, non_combat_levels)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   name              = excluded.name,
                   combat_xp         = excluded.combat_xp,
                   non_combat_xp     = excluded.non_combat_xp,
                   combat_levels     = excluded.combat_levels,
                   non_combat_levels = excluded.non_combat_levels""",
            (
                player.id,
                player.name,
                json.dumps(player.skills.combat_xp),
                json.dumps(player.skills.non_combat_xp),
                json.dumps(player.skills.combat),
                json.dumps(player.skills.non_combat),
            ),
        )
