"""Behaviour tests for every command, against the local mock API.

The cog is loaded into a real `Red` instance exactly as `[p]load` would, its
API base is pointed at a throwaway localhost server, and the key is stored
through Red's shared API token store -- the same call `[p]set api` makes.
No real API key and no outbound network access are involved.
"""

import asyncio
import importlib.util
from types import SimpleNamespace

import aiohttp
import discord
import pytest

from livetennis import livetennis as lt


class FakeContext:
    """The slice of commands.Context these command callbacks actually touch."""

    def __init__(self, bot, embed_links=True):
        self.bot = bot
        self.clean_prefix = "[p]"
        self.sent = []
        self.me = SimpleNamespace(id=1)
        perms = SimpleNamespace(embed_links=embed_links, send_messages=True)
        self.channel = SimpleNamespace(permissions_for=lambda _m: perms, id=99)
        self.guild = SimpleNamespace(id=1, me=self.me)

    def typing(self):
        ctx = self

        class _T:
            async def __aenter__(self):
                return ctx

            async def __aexit__(self, *exc):
                return False

        return _T()

    async def send(self, content=None, *, embed=None, **kwargs):
        self.sent.append(SimpleNamespace(content=content, embed=embed))
        return self.sent[-1]

    async def send_help(self):
        self.sent.append(SimpleNamespace(content="<help>", embed=None))

    # -- assertions helpers
    @property
    def last(self):
        assert self.sent, "command sent nothing"
        return self.sent[-1]

    @property
    def last_text(self):
        m = self.last
        if m.embed is not None:
            return "\n".join(
                [m.embed.title or "", m.embed.description or ""]
                + [f"{f.name}\n{f.value}" for f in m.embed.fields]
                + [(m.embed.footer.text or "") if m.embed.footer else ""]
            )
        return m.content or ""


async def load_cog(red, mock_api, monkeypatch):
    """Load the cog the way `[p]load` does, pointed at the mock API."""
    monkeypatch.setattr(lt, "API_BASE", mock_api.base_url)
    # The pytest Red instance is never `start()`ed, so `loop` is still the
    # discord.py sentinel; dispatching events (set_shared_api_tokens) needs it.
    red.loop = asyncio.get_running_loop()
    await red.load_extension(importlib.util.find_spec("livetennis"))
    return red.get_cog("LiveTennis")


@pytest.fixture()
async def cog(red, mock_api, monkeypatch):
    c = await load_cog(red, mock_api, monkeypatch)
    await red.set_shared_api_tokens("livetennis", api_key="good-key")
    yield c
    await red.remove_cog("LiveTennis")


async def set_key(red, key):
    await red.set_shared_api_tokens("livetennis", api_key=key)


# ---------------------------------------------------------------- happy paths


async def test_live_lists_matches(cog, red, mock_api):
    ctx = FakeContext(red)
    await cog.tennis_live.callback(cog, ctx, None)
    embed = ctx.last.embed
    assert isinstance(embed, discord.Embed)
    assert embed.title == "Live matches"
    text = ctx.last_text
    assert "Jannik Sinner (ITA, #1)" in text
    assert "Carlos Alcaraz (ESP, #2)" in text
    assert "6-4 3-5" in text  # per-set games, player-major
    assert "(30-40)" in text  # live game points
    assert "\N{TENNIS RACQUET AND BALL}" in text  # serving indicator
    assert "18042" in text
    # The redundant "Wimbledon - Final" round collapses to "Wimbledon — Final".
    assert "Wimbledon — Final" in text
    # The ragged doubles match with null score fields must not blow up.
    assert "doubles" in text
    assert mock_api.requests[-1][0] == "/matches"
    assert mock_api.requests[-1][1]["status"] == "live"


async def test_live_with_tour_filter(cog, red, mock_api):
    ctx = FakeContext(red)
    await cog.tennis_live.callback(cog, ctx, "atp")
    assert ctx.last.embed.title == "Live matches — ATP"
    assert mock_api.requests[-1][1]["tour"] == "atp"


async def test_live_rejects_unknown_tour_without_a_request(cog, red, mock_api):
    before = len(mock_api.requests)
    ctx = FakeContext(red)
    await cog.tennis_live.callback(cog, ctx, "pickleball")
    assert "Unknown tour" in ctx.last_text
    assert len(mock_api.requests) == before, "a bad tour must not cost a request"


async def test_upcoming_shows_relative_start_time(cog, red):
    ctx = FakeContext(red)
    await cog.tennis_upcoming.callback(cog, ctx, None)
    text = ctx.last_text
    assert "Novak Djokovic" in text
    assert "Starts <t:" in text and ":R>" in text


async def test_fixtures(cog, red, mock_api):
    ctx = FakeContext(red)
    await cog.tennis_fixtures.callback(cog, ctx, None, None)
    text = ctx.last_text
    assert "Taylor Fritz" in text and "Ben Shelton" in text
    assert "2026-08-30" in text
    assert "TBD" in text  # unresolved draw slot
    assert mock_api.requests[-1][0] == "/fixtures"
    assert mock_api.requests[-1][1]["limit"] == "25"


async def test_fixtures_limit_is_clamped(cog, red, mock_api):
    ctx = FakeContext(red)
    await cog.tennis_fixtures.callback(cog, ctx, None, 9999)
    assert mock_api.requests[-1][1]["limit"] == "200"
    ctx = FakeContext(red)
    await cog.tennis_fixtures.callback(cog, ctx, None, -5)
    assert mock_api.requests[-1][1]["limit"] == "1"


async def test_match_detail(cog, red):
    ctx = FakeContext(red)
    await cog.tennis_match.callback(cog, ctx, 18042)
    text = ctx.last_text
    assert "Jannik Sinner" in text
    assert "Live" in text
    assert "Grass" in text and "BO5" in text
    assert "Match ID: 18042" in text


async def test_match_not_found(cog, red):
    ctx = FakeContext(red)
    await cog.tennis_match.callback(cog, ctx, 1)
    assert "Not found." in ctx.last_text


async def test_score_only_endpoint(cog, red, mock_api):
    ctx = FakeContext(red)
    await cog.tennis_score.callback(cog, ctx, 18042)
    text = ctx.last_text
    assert "Live score — match 18042" in text
    assert "6-4 3-5" in text
    assert "1-1" in text  # sets
    assert "30-40" in text
    assert "Player 1" in text  # serving
    assert "62.0%" in text  # win_probability_p1 when the tier provides it
    assert mock_api.requests[-1][0] == "/matches/18042/score"


async def test_player_search(cog, red, mock_api):
    ctx = FakeContext(red)
    await cog.tennis_player.callback(cog, ctx, query="jan")
    text = ctx.last_text
    assert "Players matching" in text
    assert "Janko Tipsarevic (SRB)" in text  # null ranking omitted, not "#None"
    assert mock_api.requests[-1][1]["search"] == "jan"


async def test_player_search_single_hit_returns_profile(cog, red):
    ctx = FakeContext(red)
    await cog.tennis_player.callback(cog, ctx, query="sinner")
    text = ctx.last_text
    assert "Jannik Sinner" in text
    assert "Player ID: 1" in text


async def test_player_by_numeric_id(cog, red, mock_api):
    ctx = FakeContext(red)
    await cog.tennis_player.callback(cog, ctx, query="1")
    text = ctx.last_text
    assert "Jannik Sinner" in text
    assert "Right" in text and "Two-handed" in text and "2001-08-16" in text
    assert mock_api.requests[-1][0] == "/players/1"


async def test_status_uses_the_keyless_health_probe(cog, red, mock_api):
    ctx = FakeContext(red)
    await cog.tennis_status.callback(cog, ctx)
    assert "The Live Tennis API is up (API v1)" in ctx.last_text
    assert "An API key is configured." in ctx.last_text
    path, _qs, sent_key = mock_api.requests[-1]
    assert path == "/health"
    assert sent_key is None, "the health probe must not spend the API key"


async def test_empty_results_are_plain_text(cog, red):
    await set_key(red, "empty-key")
    ctx = FakeContext(red)
    await cog.tennis_live.callback(cog, ctx, None)
    assert ctx.last.content == "No live matches right now."
    assert ctx.last.embed is None


# ------------------------------------------------------------------ API auth


async def test_key_is_sent_as_x_api_key_header(cog, red, mock_api):
    ctx = FakeContext(red)
    await cog.tennis_live.callback(cog, ctx, None)
    assert mock_api.requests[-1][2] == "good-key"


async def test_no_key_configured(red, mock_api, monkeypatch):
    cog = await load_cog(red, mock_api, monkeypatch)
    try:
        before = len(mock_api.requests)
        ctx = FakeContext(red)
        await cog.tennis_live.callback(cog, ctx, None)
        text = ctx.last_text
        assert "No Live Tennis API key is configured" in text
        assert "[p]set api livetennis api_key" in text
        assert "https://livetennisapi.com/subscribe/free" in text
        assert len(mock_api.requests) == before, "must not call the API without a key"
    finally:
        await red.remove_cog("LiveTennis")


async def test_401_rejected_key(cog, red):
    await set_key(red, "bad-key")
    ctx = FakeContext(red)
    await cog.tennis_live.callback(cog, ctx, None)
    text = ctx.last_text
    assert "HTTP 401 unauthorized" in text
    assert ctx.last.embed.color == lt.COLOR_ERROR


async def test_403_tier_gated_endpoint(cog, red):
    ctx = FakeContext(red)
    await cog.tennis_results.callback(cog, ctx, None)
    assert "higher Live Tennis API tier" in ctx.last_text


async def test_429_reports_retry_after(cog, red):
    await set_key(red, "burst-key")
    ctx = FakeContext(red)
    await cog.tennis_live.callback(cog, ctx, None)
    text = ctx.last_text
    assert "Rate limited" in text
    assert "Try again in 30s." in text
    assert "30 req/min, 1,000/day" in text


async def test_timeout_is_handled(cog, red, monkeypatch):
    def boom(*a, **kw):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(cog.session, "get", boom)
    ctx = FakeContext(red)
    await cog.tennis_live.callback(cog, ctx, None)
    assert "timed out" in ctx.last_text


async def test_connection_error_is_handled(cog, red, monkeypatch):
    def boom(*a, **kw):
        raise aiohttp.ClientConnectionError("nope")

    monkeypatch.setattr(cog.session, "get", boom)
    ctx = FakeContext(red)
    await cog.tennis_live.callback(cog, ctx, None)
    assert "Could not reach the Live Tennis API" in ctx.last_text


async def test_errors_degrade_to_plain_text_without_embed_links(cog, red):
    await set_key(red, "bad-key")
    ctx = FakeContext(red, embed_links=False)
    await cog.tennis_live.callback(cog, ctx, None)
    assert ctx.last.embed is None
    assert "HTTP 401" in ctx.last.content


# ------------------------------------------------------------------- caching


async def test_list_responses_are_cached_for_60s(cog, red, mock_api):
    ctx = FakeContext(red)
    await cog.tennis_live.callback(cog, ctx, None)
    n = len(mock_api.requests)
    await cog.tennis_live.callback(cog, FakeContext(red), None)
    assert len(mock_api.requests) == n, "second identical query must hit the cache"


async def test_rotating_the_key_clears_the_cache(cog, red, mock_api):
    await cog.tennis_live.callback(cog, FakeContext(red), None)
    assert cog._cache
    await cog.on_red_api_tokens_update("livetennis", {"api_key": "other"})
    assert not cog._cache
    n = len(mock_api.requests)
    await cog.tennis_live.callback(cog, FakeContext(red), None)
    assert len(mock_api.requests) == n + 1


async def test_unrelated_service_token_update_is_ignored(cog, red):
    await cog.tennis_live.callback(cog, FakeContext(red), None)
    assert cog._cache
    await cog.on_red_api_tokens_update("youtube", {"api_key": "x"})
    assert cog._cache


async def test_cache_is_bounded(cog, red):
    cog._cache = {(f"/p{i}", frozenset()): (0.0, i) for i in range(lt.CACHE_MAX_ENTRIES)}
    await cog.tennis_live.callback(cog, FakeContext(red), None)
    assert len(cog._cache) < lt.CACHE_MAX_ENTRIES


# ---------------------------------------------------------------- key setting


async def test_apikey_command_never_takes_the_key_as_an_argument(cog, red):
    """The cog must not offer a way to paste a key into a channel."""
    import inspect

    params = inspect.signature(cog.tennisset_apikey.callback).parameters
    assert list(params) == ["self", "ctx"], f"unexpected params: {list(params)}"

    ctx = FakeContext(red)
    await cog.tennisset_apikey.callback(cog, ctx)
    text = ctx.last_text
    assert "[p]set api livetennis api_key" in text
    assert "https://livetennisapi.com/subscribe/free" in text
    assert "a key is set" in text


async def test_apikey_reports_when_no_key_is_set(red, mock_api, monkeypatch):
    cog = await load_cog(red, mock_api, monkeypatch)
    try:
        ctx = FakeContext(red)
        await cog.tennisset_apikey.callback(cog, ctx)
        assert "no key set" in ctx.last_text
    finally:
        await red.remove_cog("LiveTennis")


async def test_key_comes_from_reds_shared_token_store(cog, red):
    assert (await red.get_shared_api_tokens("livetennis"))["api_key"] == "good-key"
    assert await cog._get_api_key() == "good-key"
