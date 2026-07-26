"""Tests for the optional background score watch.

The watch is the only thing in this cog that spends API quota without a user
asking, so these tests pin down: it is off by default, it refuses intervals
that would blow the free tier, it seeds silently, it emits one embed per tick,
and it disables itself rather than looping on an unfixable error.
"""

import pytest

from livetennis import livetennis as lt
from .test_commands import FakeContext, load_cog


@pytest.fixture()
async def cog(red, mock_api, monkeypatch):
    c = await load_cog(red, mock_api, monkeypatch)
    # Red's JSON driver caches per cog name across the process, so start every
    # test from the registered defaults rather than the previous test's state.
    await c.config.clear_all()
    await red.set_shared_api_tokens("livetennis", api_key="good-key")
    yield c
    await red.remove_cog("LiveTennis")


async def test_watch_is_off_by_default(cog):
    assert await cog.config.watch_enabled() is False
    assert await cog.config.watch_channel() is None
    assert await cog.config.watch_interval() == lt.WATCH_DEFAULT_INTERVAL
    assert not cog.watch_loop.is_running()


async def test_watch_refuses_a_quota_burning_interval(cog, red):
    ctx = FakeContext(red)
    await cog.tennisset_watch_interval.callback(cog, ctx, lt.WATCH_MIN_INTERVAL - 1)
    assert "Minimum interval is 120s" in ctx.last_text
    assert await cog.config.watch_interval() == lt.WATCH_DEFAULT_INTERVAL


async def test_watch_warns_below_the_default_interval(cog, red):
    ctx = FakeContext(red)
    await cog.tennisset_watch_interval.callback(cog, ctx, 120)
    assert await cog.config.watch_interval() == 120
    assert "720 requests/day" in ctx.last_text


async def test_watch_on_requires_a_channel(cog, red):
    ctx = FakeContext(red)
    await cog.tennisset_watch_on.callback(cog, ctx)
    assert "Set a channel first" in ctx.last_text
    assert await cog.config.watch_enabled() is False


async def test_watch_on_requires_a_key(cog, red):
    await red.set_shared_api_tokens("livetennis", api_key="")
    await cog.config.watch_channel.set(1234)
    ctx = FakeContext(red)
    await cog.tennisset_watch_on.callback(cog, ctx)
    assert "No API key configured" in ctx.last_text
    assert await cog.config.watch_enabled() is False


async def test_watch_status_reports_quota(cog, red):
    ctx = FakeContext(red)
    await cog.tennisset_watch_status.callback(cog, ctx)
    text = ctx.last_text
    assert "Enabled: no" in text
    assert "288 requests/day" in text


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, *, embed=None, **kw):
        self.sent.append(embed if embed is not None else content)


async def test_watch_tick_seeds_silently_then_reports(cog, red, monkeypatch):
    channel = FakeChannel()
    monkeypatch.setattr(red, "get_channel", lambda _id: channel)
    await cog.config.watch_channel.set(1234)

    # First tick only seeds the snapshot.
    await cog.watch_loop.coro(cog)
    assert channel.sent == []
    assert cog._watch_prev is not None

    # Pretend the live feed emptied: every seeded match "finished".
    async def empty_feed(*a, **kw):
        return []

    monkeypatch.setattr(cog, "_matches", empty_feed)
    await cog.watch_loop.coro(cog)
    assert len(channel.sent) == 1
    embed = channel.sent[0]
    assert embed.title == "Live tennis update"
    assert "Match finished" in "\n".join(f.name for f in embed.fields)


async def test_watch_tick_costs_exactly_one_request(cog, red, mock_api, monkeypatch):
    monkeypatch.setattr(red, "get_channel", lambda _id: FakeChannel())
    await cog.config.watch_channel.set(1234)
    before = len(mock_api.requests)
    await cog.watch_loop.coro(cog)
    assert len(mock_api.requests) == before + 1


async def test_watch_disables_itself_on_a_bad_key(cog, red, monkeypatch):
    channel = FakeChannel()
    monkeypatch.setattr(red, "get_channel", lambda _id: channel)
    await cog.config.watch_channel.set(1234)
    await cog.config.watch_enabled.set(True)
    await red.set_shared_api_tokens("livetennis", api_key="bad-key")

    await cog.watch_loop.coro(cog)

    assert await cog.config.watch_enabled() is False
    assert channel.sent and "disabled" in str(channel.sent[0])


async def test_watch_disables_itself_when_the_channel_vanishes(cog, red, monkeypatch):
    monkeypatch.setattr(red, "get_channel", lambda _id: None)
    await cog.config.watch_channel.set(1234)
    await cog.config.watch_enabled.set(True)
    await cog.watch_loop.coro(cog)
    assert await cog.config.watch_enabled() is False


async def test_watch_tick_never_raises(cog, red, monkeypatch):
    """A bad tick must be swallowed, or discord.py kills the loop for good."""

    def explode(_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(red, "get_channel", explode)
    await cog.config.watch_channel.set(1234)
    await cog.watch_loop.coro(cog)  # must not raise


async def test_watch_skips_transient_errors_without_disabling(cog, red, monkeypatch):
    channel = FakeChannel()
    monkeypatch.setattr(red, "get_channel", lambda _id: channel)
    await cog.config.watch_channel.set(1234)
    await cog.config.watch_enabled.set(True)
    await red.set_shared_api_tokens("livetennis", api_key="burst-key")  # 429

    await cog.watch_loop.coro(cog)

    assert await cog.config.watch_enabled() is True
    assert channel.sent == []
