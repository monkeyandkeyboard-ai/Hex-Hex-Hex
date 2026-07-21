"""Names of queued action kinds that cross system boundaries.

Systems talk to each other by scheduling a named action on the tick engine:
one side asks, the other side owns the handler and decides what it means.
The names live here rather than in either module so that neither has to
import the other -- monster behaviour must not import combat, and combat must
not import behaviour, or damage tuning and pathfinding stop being
independently changeable (see tests/test_aggro.py).

Kinds private to a single system stay in that system.
"""

# Behaviour -> combat: this monster wants to attack that player.
# Combat owns pacing; asking more often than the weapon allows is a no-op.
MONSTER_STRIKE = "monster-strike"

# Behaviour -> abilities: this monster wants to use an ability centred on a
# tile. The abilities system owns the cooldown gate, the hostiles-only target
# set, and resolution; asking while the ability is on cooldown is a no-op.
MONSTER_ABILITY = "monster-ability"

# Combat -> respawn: this player's health hit zero. Combat reports it and
# stops caring; the defeat lifecycle is decided by the respawn system.
PLAYER_DEFEATED = "player-defeated"

# Respawn -> behaviour: forget this player exists as a target.
CLEAR_THREAT = "clear-threat"
