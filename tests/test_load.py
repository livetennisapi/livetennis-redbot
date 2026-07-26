"""Proof that `[p]load livetennis` works.

Red's `[p]load` resolves the cog package to a ModuleSpec and hands it to
`Red.load_extension(spec)`. These tests drive that exact code path against a
real `Red` instance (from Red's own pytest fixtures), so a pass means the cog
imports, its `setup()` runs, `cog_load()` runs, and every command registers on
the bot.
"""

import importlib.util

import pytest


@pytest.mark.asyncio
async def test_load_extension_registers_cog_and_commands(red):
    spec = importlib.util.find_spec("livetennis")
    assert spec is not None, "livetennis package is not importable"

    # This is what [p]load does.
    await red.load_extension(spec)

    assert "livetennis" in red.extensions
    cog = red.get_cog("LiveTennis")
    assert cog is not None

    # cog_load() ran: the aiohttp session exists and is open.
    assert cog.session is not None and not cog.session.closed

    expected = {
        "tennis",
        "tennis live",
        "tennis upcoming",
        "tennis fixtures",
        "tennis results",
        "tennis match",
        "tennis score",
        "tennis player",
        "tennis status",
        "tennisset",
        "tennisset apikey",
        "tennisset watch",
        "tennisset watch channel",
        "tennisset watch tour",
        "tennisset watch interval",
        "tennisset watch on",
        "tennisset watch off",
        "tennisset watch status",
    }
    for qualified in sorted(expected):
        assert red.get_command(qualified) is not None, f"{qualified} did not register"

    # The optional score watch must not start on its own.
    assert not cog.watch_loop.is_running()

    await red.remove_cog("LiveTennis")


@pytest.mark.asyncio
async def test_unload_closes_the_session(red):
    spec = importlib.util.find_spec("livetennis")
    await red.load_extension(spec)
    cog = red.get_cog("LiveTennis")
    session = cog.session
    assert not session.closed

    await red.remove_cog("LiveTennis")

    # cog_unload() ran: no leaked aiohttp session/connector.
    assert session.closed
    assert cog.session is None
    assert not cog.watch_loop.is_running()
    assert red.get_cog("LiveTennis") is None
    assert red.get_command("tennis live") is None


@pytest.mark.asyncio
async def test_cog_declares_red_data_api(red):
    spec = importlib.util.find_spec("livetennis")
    await red.load_extension(spec)
    cog = red.get_cog("LiveTennis")

    # Red requires cogs to expose an end-user-data deletion entry point.
    assert hasattr(cog, "red_delete_data_for_user")
    assert await cog.red_delete_data_for_user(requester="discord_deleted_user", user_id=1) is None

    await red.remove_cog("LiveTennis")
