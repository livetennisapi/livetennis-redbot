"""Unit tests for the pure formatting / diffing helpers.

These cover the nullability the API schema explicitly warns about: nullable
`score`, null entries inside `points`, empty `games` arrays on completed
matches, and a null `server`.
"""

from livetennis.livetennis import (
    CACHE_TTL,
    LIMIT_MAX,
    LIMIT_MIN,
    VALID_TOURS,
    build_fixture_embed,
    build_match_list_embed,
    build_score_embed,
    build_watch_embed,
    clamp_limit,
    clean_round,
    diff_live,
    format_conditions,
    format_games,
    format_points,
    format_score_line,
    format_sets,
    format_timestamp,
    matches_by_id,
    player_label,
    server_index,
)
import discord

# ------------------------------------------------------------------ limits


def test_clamp_limit():
    assert clamp_limit(None) == 25
    assert clamp_limit(0) == LIMIT_MIN
    assert clamp_limit(-99) == LIMIT_MIN
    assert clamp_limit(10_000) == LIMIT_MAX
    assert clamp_limit(50) == 50


def test_cache_ttl_keeps_the_free_tier_safe():
    # 30 req/min ceiling: one identical query per minute is the design intent.
    assert CACHE_TTL >= 60


def test_tour_vocabulary_matches_the_api_enum():
    assert VALID_TOURS == ("atp", "wta", "challenger", "itf", "juniors")


# ------------------------------------------------------------------- rounds


def test_clean_round_drops_redundant_round():
    assert clean_round("Wimbledon", "Wimbledon - Final") == "Final"
    assert clean_round("Wimbledon", "Wimbledon") == ""
    assert clean_round("ATP Umag", "QF") == "QF"
    assert clean_round("ATP Umag", None) == ""
    assert clean_round(None, "R16") == "R16"
    assert clean_round("ATP Umag", "  ") == ""


# ------------------------------------------------------------------ players


def test_player_label_is_null_safe():
    assert player_label(None) == "Unknown"
    assert player_label({}) == "Unknown"
    assert player_label({"name": "X"}) == "X"
    assert player_label({"name": "X", "country": "ITA"}) == "X (ITA)"
    assert player_label({"name": "X", "ranking": 3}) == "X (#3)"
    assert player_label({"name": "X", "country": "ITA", "ranking": 3}) == "X (ITA, #3)"
    # Nulls must not leak into the label.
    assert player_label({"name": "X", "country": None, "ranking": None}) == "X"


# ------------------------------------------------------------------- scores


def test_format_games_is_player_major():
    assert format_games({"games": [[6, 3], [4, 5]]}) == "6-4 3-5"
    assert format_games({"games": [[], []]}) == ""
    assert format_games({"games": None}) == ""
    assert format_games({}) == ""
    assert format_games(None) == ""
    # Ragged / partially-null rows are skipped, not crashed on.
    assert format_games({"games": [[6, None], [4, 5]]}) == "6-4"


def test_format_points_guards_nulls():
    assert format_points({"points": ["30", "40"]}) == "(30-40)"
    assert format_points({"points": [None, None]}) == ""
    assert format_points({"points": ["30", None]}) == ""
    assert format_points({"points": []}) == ""
    assert format_points(None) == ""
    assert format_points({"points": ["6", "5"], "is_tiebreak": True}) == "(6-5 TB)"


def test_format_sets():
    assert format_sets({"sets": [2, 1]}) == "Sets 2-1"
    assert format_sets({"sets": [None, 1]}) == ""
    assert format_sets({}) == ""


def test_server_index_accepts_both_wire_forms():
    assert server_index({"server": 1}) == 1
    assert server_index({"server": 2}) == 2
    assert server_index({"server": None}) is None
    assert server_index({"server": "p1"}) == 1
    assert server_index({"server": "p2"}) == 2
    assert server_index({"server": "wat"}) is None
    assert server_index(None) is None


def test_format_score_line_covers_every_state():
    assert format_score_line({"score": None}) == "Not started"
    assert (
        format_score_line(
            {"status": "live", "score": {"games": [[6], [4]], "points": ["0", "15"]}}
        )
        == "6-4 (0-15)"
    )
    # Points are only shown while live.
    assert (
        format_score_line(
            {"status": "completed", "score": {"games": [[6], [4]], "points": ["0", "15"]}}
        )
        == "6-4"
    )
    # Empty games + set counts -> fall back to the set score.
    assert (
        format_score_line({"status": "live", "score": {"games": [[], []], "sets": [1, 0]}})
        == "Sets 1-0"
    )
    # Nothing usable at all.
    assert format_score_line({"status": "live", "score": {"games": [[], []]}}) == "In progress"


def test_format_timestamp():
    assert format_timestamp("2026-07-18T13:00:00Z").startswith("<t:")
    assert format_timestamp("2026-07-18T13:00:00Z").endswith(":R>")
    assert format_timestamp(None) == ""
    assert format_timestamp("not a date") == "not a date"


def test_format_conditions():
    assert format_conditions({"surface": "grass", "format": "BO5"}) == "Grass · BO5"
    assert format_conditions({"surface": "hard", "indoor": True}) == "Hard (indoor)"
    assert format_conditions({"surface": None, "format": None}) == ""


# ------------------------------------------------------------------- embeds


def _match(mid, **kw):
    base = {
        "id": mid,
        "tournament": "T",
        "round": "R",
        "status": "live",
        "players": {"p1": {"name": "A"}, "p2": {"name": "B"}},
        "score": {"games": [[6], [4]], "sets": [1, 0], "points": ["0", "0"], "server": 1},
    }
    base.update(kw)
    return base


def test_match_list_embed_caps_at_ten_fields():
    matches = [_match(i) for i in range(25)]
    embed = build_match_list_embed(matches, title="T", color=discord.Color.green())
    assert len(embed.fields) == 10  # Discord allows 25, we cap lower for readability
    assert "15 more match(es) not shown." in embed.footer.text


def test_score_embed_omits_absent_tier_gated_fields():
    embed = build_score_embed({"games": [[6], [4]], "sets": [1, 0]}, "42")
    names = [f.name for f in embed.fields]
    assert "Win prob. (P1)" not in names
    assert "Serving" not in names
    assert embed.footer.text == "Match ID: 42"


def test_score_embed_survives_a_bad_probability():
    embed = build_score_embed({"games": [[6], [4]], "win_probability_p1": "n/a"}, "42")
    assert "Win prob. (P1)" not in [f.name for f in embed.fields]


def test_fixture_embed_handles_unresolved_slots():
    embed = build_fixture_embed(
        [{"id": 1, "player1_name": None, "player2_name": None, "tournament": None}],
        title="F",
    )
    assert "TBD" in embed.fields[0].value
    assert embed.fields[0].name == "Unknown tournament"


# -------------------------------------------------------------- watch diffing


def test_matches_by_id_skips_idless_rows():
    assert set(matches_by_id([{"id": 1}, {"no": "id"}, {"id": 2}])) == {"1", "2"}


def test_diff_live_detects_started_set_and_finished():
    prev = matches_by_id([_match(1), _match(2)])
    cur = matches_by_id(
        [
            _match(1),  # unchanged
            _match(3),  # started
        ]
    )
    cur["1"]["score"]["sets"] = [1, 1]  # set score moved
    kinds = sorted(k for k, _ in diff_live(prev, cur))
    assert kinds == ["finished", "set", "started"]


def test_diff_live_is_quiet_when_nothing_changes():
    snap = matches_by_id([_match(1), _match(2)])
    assert diff_live(snap, snap) == []


def test_watch_embed_caps_events():
    events = [("started", _match(i)) for i in range(15)]
    embed = build_watch_embed(events)
    assert len(embed.fields) == 10
    assert "5 more update(s) not shown." in embed.footer.text
