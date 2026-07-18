from gep.pathfinding import find_path, hex_dist


def always_passable(_tile):
    return True


def test_same_tile_returns_single_element_path():
    path = find_path((0, 0), (0, 0), always_passable)
    assert path == [(0, 0)]


def test_adjacent_tile_path_length_2():
    path = find_path((0, 0), (1, 0), always_passable)
    assert path is not None
    assert len(path) == 2
    assert path[0] == (0, 0)
    assert path[-1] == (1, 0)


def test_path_length_equals_hex_distance_on_open_grid():
    start, goal = (0, 0), (3, -2)
    path = find_path(start, goal, always_passable)
    assert path is not None
    assert len(path) == hex_dist(start, goal) + 1


def test_path_is_contiguous():
    path = find_path((0, 0), (5, -3), always_passable)
    assert path is not None
    for a, b in zip(path, path[1:]):
        assert hex_dist(a, b) == 1, f"non-adjacent step {a} -> {b}"


def test_blocked_tile_is_routed_around():
    # Block the direct path along q-axis; detour should exist
    blocked = {(1, 0), (2, 0)}
    path = find_path((0, 0), (3, 0), lambda t: t not in blocked)
    assert path is not None
    assert path[0] == (0, 0)
    assert path[-1] == (3, 0)
    for tile in path[1:-1]:
        assert tile not in blocked


def test_returns_none_when_goal_is_completely_walled_off():
    # Surround (0,0) with impassable tiles; goal (2,0) is unreachable
    blocked = {(1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1)}
    path = find_path((0, 0), (2, 0), lambda t: t not in blocked)
    assert path is None


def test_goal_tile_itself_need_not_be_passable_for_path_to_include_it():
    # The destination can have an entity on it (e.g. monster to attack);
    # passability check is skipped for the goal itself.
    blocked = {(1, 0)}  # goal is "blocked" but it's the goal
    path = find_path((0, 0), (1, 0), lambda t: t not in blocked)
    assert path is not None
    assert path[-1] == (1, 0)
