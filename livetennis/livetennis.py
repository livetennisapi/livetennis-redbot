"""LiveTennis - live tennis scores for Red-DiscordBot, powered by livetennisapi.com.

API reference: https://docs.livetennisapi.com/ (OpenAPI: openapi.yaml)
Base URL:      https://api.livetennisapi.com/api/public/v1
Auth:          ``x-api-key: <key>`` (``Authorization: Bearer <key>`` also accepted)
Free key:      https://livetennisapi.com/subscribe/free  (30 req/min, 1,000/day)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.livetennis")

API_BASE = "https://api.livetennisapi.com/api/public/v1"
SERVICE_NAME = "livetennis"
SIGNUP_URL = "https://livetennisapi.com/subscribe/free"

# `tour` filter vocabulary (OpenAPI components/parameters/tour). An unrecognised
# value is a 400 from the API, so we validate client-side and never spend a request.
VALID_TOURS = ("atp", "wta", "challenger", "itf", "juniors")

# OpenAPI components/parameters/limit: integer, default 50, min 1, max 200.
LIMIT_MIN = 1
LIMIT_MAX = 200
DEFAULT_LIST_LIMIT = 25

# Free tier is 30 req/min & 1,000/day: cache list responses and never re-fetch
# the same query more than once per 60 seconds.
CACHE_TTL = 60
CACHE_MAX_ENTRIES = 128

# Score-watch quota math (free tier = 1,000 requests/day):
#   each watch tick costs exactly ONE request (GET /matches?status=live).
#   86400 / interval = requests/day:
#     300 s (default) ->  288/day  (~29% of the free quota)
#     120 s (minimum) ->  720/day  (~72% of the free quota)
# Intervals below 120 s are refused outright; below 300 s we warn that little
# quota is left for interactive commands. Finished matches are announced from
# the last cached live snapshot -- the watcher never spends extra requests on
# match-detail lookups.
WATCH_MIN_INTERVAL = 120
WATCH_DEFAULT_INTERVAL = 300
WATCH_MAX_EVENTS = 10

MAX_EMBED_FIELDS = 10

COLOR_LIVE = discord.Color.green()
COLOR_UPCOMING = discord.Color.blurple()
COLOR_COMPLETED = discord.Color.dark_grey()
COLOR_ERROR = discord.Color.red()

NO_KEY_MSG = (
    "No Live Tennis API key is configured. The bot owner can set one with\n"
    "`{prefix}set api livetennis api_key <your key>`\n"
    f"Grab a free key (30 req/min, 1,000/day) at <{SIGNUP_URL}>."
)

UPGRADE_MSG = (
    "That data needs a higher Live Tennis API tier than the configured key has. "
    "See <https://livetennisapi.com> for plans."
)


# ---------------------------------------------------------------------------
# Pure formatting helpers (module-level so they are trivial to unit-test)
# ---------------------------------------------------------------------------


def clamp_limit(limit: Optional[int], default: int = DEFAULT_LIST_LIMIT) -> int:
    """Clamp a user-supplied limit into the API's documented 1..200 range."""
    if limit is None:
        return default
    return max(LIMIT_MIN, min(LIMIT_MAX, int(limit)))


def clean_round(tournament: Optional[str], round_: Optional[str]) -> str:
    """Return a display-worthy round name, or "" when the round merely
    restates the tournament name.

    The API's ``round`` field often repeats the tournament (sometimes as
    ``"<Tournament> - <Round>"``). We take the last ``" - "`` segment and
    drop it if it and the tournament contain each other either way.
    """
    if not round_:
        return ""
    seg = str(round_).split(" - ")[-1].strip()
    if not seg:
        return ""
    t = (tournament or "").strip().lower()
    s = seg.lower()
    if t and (s in t or t in s):
        return ""
    return seg


def player_label(player: Optional[Dict[str, Any]]) -> str:
    """``Name (CC, #rank)`` with every part optional-safe.

    Per the Player schema, ``country`` and ``ranking`` are both nullable.
    """
    if not player:
        return "Unknown"
    name = player.get("name") or "Unknown"
    extras = []
    if player.get("country"):
        extras.append(str(player["country"]))
    if player.get("ranking"):
        extras.append(f"#{player['ranking']}")
    return f"{name} ({', '.join(extras)})" if extras else name


def format_games(score: Optional[Dict[str, Any]]) -> str:
    """Per-set games as ``6-4 3-6``.

    Score.games is PLAYER-major: ``[[p1 set1, p1 set2, ...], [p2 set1, ...]]``.
    Completed matches are observed live carrying empty games arrays.
    """
    if not score:
        return ""
    games = score.get("games")
    if not isinstance(games, (list, tuple)) or len(games) < 2:
        return ""
    p1 = games[0] if isinstance(games[0], (list, tuple)) else []
    p2 = games[1] if isinstance(games[1], (list, tuple)) else []
    parts = []
    for a, b in zip(p1, p2):
        if a is None or b is None:
            continue
        parts.append(f"{a}-{b}")
    return " ".join(parts)


def format_points(score: Optional[Dict[str, Any]]) -> str:
    """Current game points as ``(40-AD)``.

    Score.points entries are tennis strings and are explicitly nullable
    (observed live on completed matches), so both entries are guarded.
    """
    if not score:
        return ""
    pts = score.get("points")
    if (
        isinstance(pts, (list, tuple))
        and len(pts) >= 2
        and pts[0] is not None
        and pts[1] is not None
    ):
        label = "TB" if score.get("is_tiebreak") else ""
        return f"({pts[0]}-{pts[1]}{' ' + label if label else ''})"
    return ""


def format_sets(score: Optional[Dict[str, Any]]) -> str:
    """Set counts as ``Sets 2-1`` (Score.sets is ``[p1_sets, p2_sets]``)."""
    if not score:
        return ""
    sets = score.get("sets")
    if (
        isinstance(sets, (list, tuple))
        and len(sets) >= 2
        and sets[0] is not None
        and sets[1] is not None
    ):
        return f"Sets {sets[0]}-{sets[1]}"
    return ""


def format_score_line(match: Dict[str, Any]) -> str:
    """Full score line for a match: per-set games plus live points."""
    score = match.get("score")
    if not score:
        return "Not started"
    pieces = []
    games = format_games(score)
    if games:
        pieces.append(games)
    if (match.get("status") or "").lower() == "live":
        pts = format_points(score)
        if pts:
            pieces.append(pts)
    if not pieces:
        # Fall back to set counts if per-set games are unusable.
        return format_sets(score) or "In progress"
    return " ".join(pieces)


def server_index(score: Optional[Dict[str, Any]]) -> Optional[int]:
    """Normalise Score.server to 1, 2 or None.

    The schema types it as integer 1|2|null, but the published sample response
    on livetennisapi.com renders it as ``"p1"``. Accept both rather than
    silently dropping the serving indicator if the wire format varies.
    """
    if not score:
        return None
    raw = score.get("server")
    if raw in (1, 2):
        return int(raw)
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in ("1", "p1"):
            return 1
        if s in ("2", "p2"):
            return 2
    return None


def format_players_line(match: Dict[str, Any]) -> str:
    """``**P1** vs **P2**`` with a ball marker on the server and a trophy on
    the winner (``winner`` is populated on completed matches only)."""
    players = match.get("players") or {}
    p1 = players.get("p1") or {}
    p2 = players.get("p2") or {}
    server = server_index(match.get("score"))
    winner = match.get("winner")
    s1 = " \N{TENNIS RACQUET AND BALL}" if server == 1 else ""
    s2 = " \N{TENNIS RACQUET AND BALL}" if server == 2 else ""
    w1 = "\N{TROPHY} " if winner == 1 else ""
    w2 = "\N{TROPHY} " if winner == 2 else ""
    line = f"{w1}**{player_label(p1)}**{s1} vs {w2}**{player_label(p2)}**{s2}"
    if match.get("is_doubles"):
        line += " *(doubles)*"
    return line


def format_timestamp(raw: Any, style: str = "R") -> str:
    """Render an ISO-8601 value as a Discord timestamp, or echo it back."""
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return f"<t:{int(dt.timestamp())}:{style}>"
    except (ValueError, OverflowError, OSError):
        return str(raw)


def match_header(match: Dict[str, Any]) -> str:
    """``Tournament — Round`` with the redundant-round dedupe applied."""
    tournament = match.get("tournament") or "Unknown tournament"
    rnd = clean_round(tournament, match.get("round"))
    return f"{tournament} — {rnd}" if rnd else tournament


def format_conditions(match: Dict[str, Any]) -> str:
    """``Hard (indoor) · BO3`` from the nullable surface/indoor/format fields."""
    bits = []
    surface = match.get("surface")
    if surface:
        bits.append(str(surface).title() + (" (indoor)" if match.get("indoor") else ""))
    if match.get("format"):
        bits.append(str(match["format"]))
    return " · ".join(bits)


def build_match_list_embed(
    matches: List[Dict[str, Any]],
    *,
    title: str,
    color: discord.Color,
    show_schedule: bool = False,
    footer: str = "",
) -> discord.Embed:
    """One embed listing up to MAX_EMBED_FIELDS matches, one field per match."""
    embed = discord.Embed(title=title, color=color)
    for m in matches[:MAX_EMBED_FIELDS]:
        lines = [format_players_line(m)]
        if show_schedule and not m.get("score"):
            sched = format_timestamp(m.get("scheduled_time"))
            if sched:
                lines.append(f"Starts {sched}")
        else:
            lines.append(format_score_line(m))
        lines.append(f"`{m.get('id', '?')}`")
        embed.add_field(name=match_header(m), value="\n".join(lines), inline=False)
    extra = len(matches) - MAX_EMBED_FIELDS
    tail = f"{extra} more match(es) not shown." if extra > 0 else ""
    footer_text = " ".join(x for x in (tail, footer) if x)
    if footer_text:
        embed.set_footer(text=footer_text)
    return embed


def build_match_embed(match: Dict[str, Any]) -> discord.Embed:
    """Detailed embed for a single match (GET /matches/{id})."""
    status = (match.get("status") or "unknown").lower()
    color = {
        "live": COLOR_LIVE,
        "upcoming": COLOR_UPCOMING,
        "completed": COLOR_COMPLETED,
        "cancelled": COLOR_ERROR,
    }.get(status, COLOR_UPCOMING)
    embed = discord.Embed(title=match_header(match), color=color)
    embed.add_field(name="Players", value=format_players_line(match), inline=False)
    embed.add_field(name="Status", value=status.title(), inline=True)
    if match.get("score"):
        embed.add_field(name="Score", value=format_score_line(match), inline=True)
        sets = format_sets(match["score"])
        if sets:
            embed.add_field(name="Sets", value=sets.replace("Sets ", ""), inline=True)
    conditions = format_conditions(match)
    if conditions:
        embed.add_field(name="Conditions", value=conditions, inline=True)
    sched = format_timestamp(match.get("scheduled_time"))
    if sched:
        embed.add_field(name="Scheduled", value=sched, inline=True)
    if match.get("id") is not None:
        embed.set_footer(text=f"Match ID: {match['id']}")
    return embed


def build_score_embed(score: Dict[str, Any], match_id: str) -> discord.Embed:
    """Embed for GET /matches/{id}/score, which returns a bare Score object
    with no match context (it is the lowest-latency read on the API)."""
    embed = discord.Embed(title=f"Live score — match {match_id}", color=COLOR_LIVE)
    games = format_games(score)
    embed.add_field(name="Games", value=games or "—", inline=False)
    sets = format_sets(score)
    if sets:
        embed.add_field(name="Sets", value=sets.replace("Sets ", ""), inline=True)
    pts = format_points(score)
    if pts:
        embed.add_field(name="Point", value=pts.strip("()"), inline=True)
    srv = server_index(score)
    if srv:
        embed.add_field(name="Serving", value=f"Player {srv}", inline=True)
    if score.get("is_tiebreak"):
        embed.add_field(name="Tiebreak", value="Yes", inline=True)
    # win_probability_p1 / danger are ULTRA-tier and absent on lower tiers.
    if score.get("win_probability_p1") is not None:
        try:
            embed.add_field(
                name="Win prob. (P1)",
                value=f"{float(score['win_probability_p1']) * 100:.1f}%",
                inline=True,
            )
        except (TypeError, ValueError):
            pass
    ts = format_timestamp(score.get("timestamp"))
    if ts:
        embed.add_field(name="Updated", value=ts, inline=True)
    embed.set_footer(text=f"Match ID: {match_id}")
    return embed


def build_player_embed(player: Dict[str, Any]) -> discord.Embed:
    """Embed for a Player record (list entries omit the `stats` object)."""
    embed = discord.Embed(title=player.get("name") or "Unknown player", color=COLOR_UPCOMING)
    if player.get("country"):
        embed.add_field(name="Country", value=str(player["country"]), inline=True)
    if player.get("ranking"):
        arrow = {"up": " \N{UPWARDS BLACK ARROW}", "down": " \N{DOWNWARDS BLACK ARROW}"}.get(
            player.get("ranking_movement") or "", ""
        )
        embed.add_field(name="Ranking", value=f"#{player['ranking']}{arrow}", inline=True)
    if player.get("ranking_points"):
        embed.add_field(name="Points", value=str(player["ranking_points"]), inline=True)
    hand = {"R": "Right", "L": "Left"}.get(player.get("hand") or "")
    if hand:
        embed.add_field(name="Plays", value=hand, inline=True)
    backhand = {1: "One-handed", 2: "Two-handed"}.get(player.get("backhand"))
    if backhand:
        embed.add_field(name="Backhand", value=backhand, inline=True)
    if player.get("birthday"):
        embed.add_field(name="Born", value=str(player["birthday"]), inline=True)
    if player.get("is_doubles_team"):
        embed.add_field(name="Type", value="Doubles team", inline=True)
    if player.get("id") is not None:
        embed.set_footer(text=f"Player ID: {player['id']}")
    return embed


def fixture_header(fixture: Dict[str, Any]) -> str:
    tournament = fixture.get("tournament") or "Unknown tournament"
    rnd = clean_round(tournament, fixture.get("round"))
    return f"{tournament} — {rnd}" if rnd else tournament


def build_fixture_embed(fixtures: List[Dict[str, Any]], *, title: str) -> discord.Embed:
    """Embed for GET /fixtures.

    Fixtures are a different shape from matches: players are name-only strings
    (not yet resolved to player ids) and the schedule is a bare ``event_date``.
    """
    embed = discord.Embed(title=title, color=COLOR_UPCOMING)
    for f in fixtures[:MAX_EMBED_FIELDS]:
        p1 = f.get("player1_name") or "TBD"
        p2 = f.get("player2_name") or "TBD"
        lines = [f"**{p1}** vs **{p2}**"]
        meta = []
        if f.get("event_date"):
            meta.append(str(f["event_date"]))
        if f.get("surface"):
            meta.append(str(f["surface"]).title())
        if f.get("tour"):
            meta.append(str(f["tour"]))
        if meta:
            lines.append(" · ".join(meta))
        lines.append(f"`{f.get('id', '?')}`")
        embed.add_field(name=fixture_header(f), value="\n".join(lines), inline=False)
    extra = len(fixtures) - MAX_EMBED_FIELDS
    if extra > 0:
        embed.set_footer(text=f"{extra} more fixture(s) not shown.")
    return embed


# ---------------------------------------------------------------------------
# Score-watch diffing (pure, so it is easy to test)
# ---------------------------------------------------------------------------


def matches_by_id(matches: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Index a live-match list by id, skipping entries without one."""
    out: Dict[str, Dict[str, Any]] = {}
    for m in matches:
        mid = m.get("id")
        if mid is not None:
            out[str(mid)] = m
    return out


def _set_state(match: Dict[str, Any]) -> Optional[Tuple[Any, Any]]:
    sets = (match.get("score") or {}).get("sets")
    if isinstance(sets, (list, tuple)) and len(sets) >= 2:
        return (sets[0], sets[1])
    return None


def diff_live(
    prev: Dict[str, Dict[str, Any]], cur: Dict[str, Dict[str, Any]]
) -> List[Tuple[str, Dict[str, Any]]]:
    """Diff two live-match snapshots into watch events.

    Returns ``(kind, match)`` tuples, kind in ``{"started", "set", "finished"}``.
    "finished" carries the *previous* (last seen) match dict -- the match has
    left the live feed, and we deliberately do not spend an extra API request
    confirming the final score.
    """
    events: List[Tuple[str, Dict[str, Any]]] = []
    for mid, m in cur.items():
        if mid not in prev:
            events.append(("started", m))
        elif _set_state(m) != _set_state(prev[mid]):
            events.append(("set", m))
    for mid, m in prev.items():
        if mid not in cur:
            events.append(("finished", m))
    return events


WATCH_EVENT_LABELS = {
    "started": "\N{LARGE GREEN CIRCLE} Match started",
    "set": "\N{TENNIS RACQUET AND BALL} Set score changed",
    "finished": "\N{CHEQUERED FLAG} Match finished",
}


def build_watch_embed(events: List[Tuple[str, Dict[str, Any]]]) -> discord.Embed:
    """One embed summarizing a watch tick's events (capped at WATCH_MAX_EVENTS)."""
    embed = discord.Embed(title="Live tennis update", color=COLOR_LIVE)
    for kind, m in events[:WATCH_MAX_EVENTS]:
        label = WATCH_EVENT_LABELS.get(kind, kind)
        lines = [format_players_line(m), format_score_line(m), f"`{m.get('id', '?')}`"]
        embed.add_field(
            name=f"{label} — {match_header(m)}",
            value="\n".join(x for x in lines if x),
            inline=False,
        )
    extra = len(events) - WATCH_MAX_EVENTS
    if extra > 0:
        embed.set_footer(text=f"{extra} more update(s) not shown.")
    return embed


# ---------------------------------------------------------------------------
# The cog
# ---------------------------------------------------------------------------


class ApiError(Exception):
    """Raised for non-2xx API responses; carries a user-facing message."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class LiveTennis(commands.Cog):
    """Live tennis scores, matches and player lookups from livetennisapi.com."""

    __author__ = "Live Tennis API"
    __version__ = "1.2.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        # (path, frozenset(params)) -> (fetched_at_monotonic, payload)
        self._cache: Dict[Tuple[str, frozenset], Tuple[float, Any]] = {}
        self.config = Config.get_conf(self, identifier=0x11FE7E9915, force_registration=True)
        self.config.register_global(
            watch_enabled=False,
            watch_channel=None,
            watch_tour=None,
            watch_interval=WATCH_DEFAULT_INTERVAL,
        )
        # Last live snapshot, {match_id: match}; None = seed silently next tick.
        self._watch_prev: Optional[Dict[str, Dict[str, Any]]] = None

    # -- lifecycle ----------------------------------------------------------

    async def cog_load(self) -> None:
        """Own the aiohttp session for exactly the cog's lifetime."""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": f"LiveTennis-Red/{self.__version__}"},
        )
        interval = await self.config.watch_interval()
        self.watch_loop.change_interval(seconds=max(WATCH_MIN_INTERVAL, int(interval)))
        if await self.config.watch_enabled():
            self.watch_loop.start()

    async def cog_unload(self) -> None:
        """Cancel the watch and close the session -- no leaked connectors."""
        self.watch_loop.cancel()
        if self.session is not None:
            await self.session.close()
            self.session = None
        self._cache.clear()

    async def red_delete_data_for_user(self, **kwargs) -> None:
        """This cog stores no end user data."""
        return

    def format_help_for_context(self, ctx: commands.Context) -> str:
        pre = super().format_help_for_context(ctx)
        return f"{pre}\n\nCog Version: {self.__version__}\nAuthor: {self.__author__}"

    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name: str, api_tokens: dict) -> None:
        """Drop cached responses when the owner rotates the key."""
        if service_name == SERVICE_NAME:
            self._cache.clear()
            self._watch_prev = None

    # -- API plumbing -------------------------------------------------------

    async def _get_api_key(self) -> Optional[str]:
        tokens = await self.bot.get_shared_api_tokens(SERVICE_NAME)
        return tokens.get("api_key")

    async def _request(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        cache: bool = False,
        needs_key: bool = True,
    ) -> Any:
        """GET ``path`` and return the parsed JSON body.

        Raises :class:`ApiError` with a user-facing message on every failure
        path, so callers only ever have one exception type to handle.
        List endpoints are cached for CACHE_TTL seconds to respect the free
        tier (30 requests/min, 1,000/day).
        """
        if self.session is None or self.session.closed:
            raise ApiError("The cog is not ready yet. Try again in a moment.")
        params = params or {}
        cache_key = (path, frozenset(params.items()))
        if cache:
            hit = self._cache.get(cache_key)
            if hit and (time.monotonic() - hit[0]) < CACHE_TTL:
                return hit[1]

        headers = {}
        if needs_key:
            key = await self._get_api_key()
            if not key:
                raise ApiError(NO_KEY_MSG)
            headers["x-api-key"] = key

        try:
            async with self.session.get(
                f"{API_BASE}{path}", params=params, headers=headers
            ) as resp:
                if resp.status == 401:
                    raise ApiError(
                        "The configured Live Tennis API key was rejected "
                        "(HTTP 401 unauthorized). The bot owner should check it "
                        f"and set it again -- free keys at <{SIGNUP_URL}>."
                    )
                if resp.status == 403:
                    raise ApiError(UPGRADE_MSG)
                if resp.status == 429:
                    retry_after = resp.headers.get("Retry-After")
                    wait = f" Try again in {retry_after}s." if retry_after else ""
                    raise ApiError(
                        "Rate limited by the Live Tennis API "
                        f"(free tier: 30 req/min, 1,000/day).{wait}"
                    )
                if resp.status == 404:
                    raise ApiError("Not found.")
                if resp.status == 400:
                    raise ApiError("The Live Tennis API rejected that request (HTTP 400).")
                if resp.status >= 400:
                    raise ApiError(f"The Live Tennis API returned HTTP {resp.status}.")
                try:
                    data = await resp.json()
                except (aiohttp.ContentTypeError, ValueError) as exc:
                    log.warning("Live Tennis API returned non-JSON: %s", exc)
                    raise ApiError("The Live Tennis API returned an unreadable response.")
        except asyncio.TimeoutError:
            log.warning("Live Tennis API request to %s timed out", path)
            raise ApiError("The Live Tennis API timed out. Try again later.")
        except aiohttp.ClientError as exc:
            log.warning("Live Tennis API request to %s failed: %s", path, exc)
            raise ApiError("Could not reach the Live Tennis API. Try again later.")

        if cache:
            if len(self._cache) >= CACHE_MAX_ENTRIES:
                now = time.monotonic()
                self._cache = {k: v for k, v in self._cache.items() if (now - v[0]) < CACHE_TTL}
            self._cache[cache_key] = (time.monotonic(), data)
        return data

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        """List endpoints wrap results as ``{"data": [...], "meta": {...}}``;
        detail endpoints (``/matches/{id}``, ``/matches/{id}/score``,
        ``/players/{id}``) return the object at the top level. Accept both."""
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    @staticmethod
    def _parse_tour(tour: Optional[str]) -> Optional[str]:
        """Validate the tour filter locally -- the API 400s on unknown values."""
        if tour is None:
            return None
        tour = tour.lower().strip()
        if tour not in VALID_TOURS:
            raise ApiError(f"Unknown tour `{tour}`. Valid tours: {', '.join(VALID_TOURS)}.")
        return tour

    async def _matches(
        self, status: str, tour: Optional[str], limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"status": status, "limit": clamp_limit(limit)}
        t = self._parse_tour(tour)
        if t:
            params["tour"] = t
        payload = await self._request("/matches", params, cache=True)
        data = self._unwrap(payload)
        return data if isinstance(data, list) else []

    async def _send_error(self, ctx: commands.Context, message: str) -> None:
        message = message.format(prefix=ctx.clean_prefix)
        if ctx.channel.permissions_for(ctx.me).embed_links:
            await ctx.send(embed=discord.Embed(description=message, color=COLOR_ERROR))
        else:
            await ctx.send(message)

    # -- commands: tennis ---------------------------------------------------

    @commands.group()
    async def tennis(self, ctx: commands.Context):
        """Live tennis scores from livetennisapi.com."""

    @tennis.command(name="live")
    @commands.bot_has_permissions(embed_links=True)
    @commands.cooldown(1, 5, commands.BucketType.channel)
    async def tennis_live(self, ctx: commands.Context, tour: Optional[str] = None):
        """Show live matches, optionally for one tour.

        Tours: atp, wta, challenger, itf, juniors.
        Results are cached for 60 seconds to protect the API quota.
        """
        async with ctx.typing():
            try:
                matches = await self._matches("live", tour)
            except ApiError as e:
                return await self._send_error(ctx, e.message)
            if not matches:
                return await ctx.send("No live matches right now.")
            title = f"Live matches — {tour.upper()}" if tour else "Live matches"
            embed = build_match_list_embed(
                matches,
                title=title,
                color=COLOR_LIVE,
                footer="Scores refresh at most once per 60s.",
            )
        await ctx.send(embed=embed)

    @tennis.command(name="upcoming")
    @commands.bot_has_permissions(embed_links=True)
    @commands.cooldown(1, 5, commands.BucketType.channel)
    async def tennis_upcoming(self, ctx: commands.Context, tour: Optional[str] = None):
        """Show upcoming matches, optionally for one tour.

        These are scheduled matches that already have resolved player records.
        For the raw draw sheet, see `[p]tennis fixtures`.
        """
        async with ctx.typing():
            try:
                matches = await self._matches("upcoming", tour)
            except ApiError as e:
                return await self._send_error(ctx, e.message)
            if not matches:
                return await ctx.send("No upcoming matches found.")
            title = f"Upcoming matches — {tour.upper()}" if tour else "Upcoming matches"
            embed = build_match_list_embed(
                matches, title=title, color=COLOR_UPCOMING, show_schedule=True
            )
        await ctx.send(embed=embed)

    @tennis.command(name="fixtures")
    @commands.bot_has_permissions(embed_links=True)
    @commands.cooldown(1, 5, commands.BucketType.channel)
    async def tennis_fixtures(
        self, ctx: commands.Context, tour: Optional[str] = None, limit: Optional[int] = None
    ):
        """Show upcoming scheduled fixtures, earliest first.

        Fixtures are name-only (players are not yet resolved to player IDs).
        Tours: atp, wta, challenger, itf, juniors. Limit is clamped to 1-200.
        """
        async with ctx.typing():
            params: Dict[str, Any] = {"limit": clamp_limit(limit)}
            try:
                t = self._parse_tour(tour)
                if t:
                    params["tour"] = t
                payload = await self._request("/fixtures", params, cache=True)
            except ApiError as e:
                return await self._send_error(ctx, e.message)
            fixtures = self._unwrap(payload)
            if not isinstance(fixtures, list) or not fixtures:
                return await ctx.send("No upcoming fixtures found.")
            title = f"Fixtures — {tour.upper()}" if tour else "Upcoming fixtures"
            embed = build_fixture_embed(fixtures, title=title)
        await ctx.send(embed=embed)

    @tennis.command(name="results")
    @commands.bot_has_permissions(embed_links=True)
    @commands.cooldown(1, 5, commands.BucketType.channel)
    async def tennis_results(self, ctx: commands.Context, tour: Optional[str] = None):
        """Show recently completed matches, optionally for one tour.

        Note: `status=completed` is a BASIC-tier endpoint. On a free key the
        API answers 403 and the cog will say so.
        """
        async with ctx.typing():
            try:
                matches = await self._matches("completed", tour)
            except ApiError as e:
                return await self._send_error(ctx, e.message)
            if not matches:
                return await ctx.send("No completed matches found.")
            title = f"Results — {tour.upper()}" if tour else "Results"
            embed = build_match_list_embed(matches, title=title, color=COLOR_COMPLETED)
        await ctx.send(embed=embed)

    @tennis.command(name="match")
    @commands.bot_has_permissions(embed_links=True)
    @commands.cooldown(1, 5, commands.BucketType.channel)
    async def tennis_match(self, ctx: commands.Context, match_id: int):
        """Show one match in detail by its numeric ID.

        Match IDs are shown in `[p]tennis live` / `upcoming` / `results`.
        """
        async with ctx.typing():
            try:
                payload = await self._request(f"/matches/{match_id}")
            except ApiError as e:
                return await self._send_error(ctx, e.message)
            match = self._unwrap(payload)
            if not isinstance(match, dict) or not match:
                return await ctx.send("Match not found.")
        await ctx.send(embed=build_match_embed(match))

    @tennis.command(name="score")
    @commands.bot_has_permissions(embed_links=True)
    @commands.cooldown(1, 5, commands.BucketType.channel)
    async def tennis_score(self, ctx: commands.Context, match_id: int):
        """Show just the current score for one match (fastest read).

        Uses the score-only endpoint, which returns the live score without
        the surrounding match record.
        """
        async with ctx.typing():
            try:
                payload = await self._request(f"/matches/{match_id}/score")
            except ApiError as e:
                return await self._send_error(ctx, e.message)
            score = self._unwrap(payload)
            if not isinstance(score, dict) or not score:
                return await ctx.send("No score available for that match.")
        await ctx.send(embed=build_score_embed(score, str(match_id)))

    @tennis.command(name="player")
    @commands.bot_has_permissions(embed_links=True)
    @commands.cooldown(1, 5, commands.BucketType.channel)
    async def tennis_player(self, ctx: commands.Context, *, query: str):
        """Look up a player by name, or by numeric player ID.

        A numeric argument is treated as a player ID and returns that player's
        full record; anything else is a name search.
        """
        query = query.strip()
        async with ctx.typing():
            if query.isdigit():
                try:
                    payload = await self._request(f"/players/{query}", cache=True)
                except ApiError as e:
                    return await self._send_error(ctx, e.message)
                player = self._unwrap(payload)
                if not isinstance(player, dict) or not player:
                    return await ctx.send(f"No player with ID `{query}`.")
                return await ctx.send(embed=build_player_embed(player))

            try:
                payload = await self._request(
                    "/players",
                    {"search": query, "limit": clamp_limit(None)},
                    cache=True,
                )
            except ApiError as e:
                return await self._send_error(ctx, e.message)
            players = self._unwrap(payload)
            if not isinstance(players, list) or not players:
                return await ctx.send(f"No players found matching `{query}`.")
            if len(players) == 1:
                return await ctx.send(embed=build_player_embed(players[0]))
            embed = discord.Embed(title=f"Players matching “{query}”", color=COLOR_UPCOMING)
            embed.description = "\n".join(
                f"{player_label(p)} — `{p.get('id', '?')}`" for p in players[:MAX_EMBED_FIELDS]
            )
            if len(players) > MAX_EMBED_FIELDS:
                embed.set_footer(
                    text=f"{len(players) - MAX_EMBED_FIELDS} more not shown. "
                    f"Use {ctx.clean_prefix}tennis player <id> for one player."
                )
        await ctx.send(embed=embed)

    @tennis.command(name="status")
    @commands.cooldown(1, 10, commands.BucketType.guild)
    async def tennis_status(self, ctx: commands.Context):
        """Check whether the Live Tennis API is reachable.

        Uses the public liveness probe, which needs no API key.
        """
        async with ctx.typing():
            try:
                payload = await self._request("/health", needs_key=False)
            except ApiError as e:
                return await self._send_error(ctx, f"API health check failed: {e.message}")
            version = ""
            if isinstance(payload, dict) and payload.get("version"):
                version = f" (API {payload['version']})"
            has_key = bool(await self._get_api_key())
        key_note = (
            "An API key is configured."
            if has_key
            else f"No API key is configured -- see `{ctx.clean_prefix}tennisset apikey`."
        )
        await ctx.send(
            f"\N{WHITE HEAVY CHECK MARK} The Live Tennis API is up{version}. {key_note}"
        )

    # -- commands: tennisset ------------------------------------------------

    @commands.group()
    @commands.is_owner()
    async def tennisset(self, ctx: commands.Context):
        """Configure the LiveTennis cog (bot owner only)."""

    @tennisset.command(name="apikey")
    async def tennisset_apikey(self, ctx: commands.Context):
        """Explain how to set the Live Tennis API key.

        The key is stored with Red's shared API token store, so it is set
        through Red's own `[p]set api` command rather than being pasted as an
        argument to this cog. `[p]set api` can be run in DM and offers a
        modal in supported clients, so the key never has to appear in a
        channel.
        """
        has_key = bool(await self._get_api_key())
        msg = (
            "**Live Tennis API key**\n"
            f"1. Get a free key (30 req/min, 1,000/day) at <{SIGNUP_URL}>\n"
            f"2. Run (ideally in DM): `{ctx.clean_prefix}set api livetennis api_key <your key>`\n"
            f"   or just `{ctx.clean_prefix}set api` and pick `livetennis`.\n\n"
            f"Current status: **{'a key is set' if has_key else 'no key set'}**."
        )
        await ctx.send(msg)

    # -- score watch --------------------------------------------------------

    @tasks.loop(seconds=WATCH_DEFAULT_INTERVAL)
    async def watch_loop(self) -> None:
        """Background score watch: ONE live-list request per tick.

        Quota: 86400/interval requests/day -- 288/day at the 300 s default,
        720/day at the 120 s floor, against the free tier's 1,000/day.
        """
        try:
            channel_id = await self.config.watch_channel()
            channel = self.bot.get_channel(channel_id) if channel_id else None
            if channel is None:
                log.warning("Score watch: channel %s not found; disabling the watch.", channel_id)
                await self._watch_disable()
                return
            tour = await self.config.watch_tour()
            try:
                matches = await self._matches("live", tour)
            except ApiError as e:
                # Config errors (no key / bad key) will not fix themselves:
                # disable and tell the channel once. Transient errors just skip.
                if "key" in e.message.lower():
                    log.warning("Score watch: %s -- disabling the watch.", e.message)
                    await self._watch_disable()
                    try:
                        await channel.send(
                            "Live tennis score watch disabled: " + e.message.format(prefix="[p]")
                        )
                    except discord.HTTPException:
                        pass
                else:
                    log.info("Score watch tick skipped: %s", e.message)
                return
            cur = matches_by_id(matches)
            prev, self._watch_prev = self._watch_prev, cur
            if prev is None:
                return  # first tick after (re)start: seed silently
            events = diff_live(prev, cur)
            if not events:
                return
            try:
                await channel.send(embed=build_watch_embed(events))
            except discord.Forbidden:
                log.warning(
                    "Score watch: cannot send to channel %s; disabling the watch.",
                    channel_id,
                )
                await self._watch_disable()
            except discord.HTTPException as exc:
                log.warning("Score watch: failed to send update: %s", exc)
        except Exception:  # never let one bad tick kill the loop
            log.exception("Score watch tick failed")

    @watch_loop.before_loop
    async def watch_loop_before(self) -> None:
        await self.bot.wait_until_red_ready()

    async def _watch_disable(self) -> None:
        await self.config.watch_enabled.set(False)
        self._watch_prev = None
        self.watch_loop.cancel()

    @tennisset.group(name="watch")
    async def tennisset_watch(self, ctx: commands.Context):
        """Configure the background score watch (owner only).

        When enabled, the cog polls the live-match list once per interval
        (default 300s, minimum 120s) and posts started/set/finished updates
        to one channel. Each tick costs exactly one API request:
        300s = 288 requests/day, 120s = 720/day (free tier: 1,000/day).
        Disabled by default.
        """

    @tennisset_watch.command(name="channel")
    @commands.guild_only()
    async def tennisset_watch_channel(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ):
        """Set (or show) the channel score updates are posted to."""
        if channel is None:
            cid = await self.config.watch_channel()
            return await ctx.send(
                f"Score updates go to <#{cid}>." if cid else "No watch channel set."
            )
        perms = channel.permissions_for(channel.guild.me)
        if not perms.send_messages:
            return await ctx.send("I can't send messages in that channel.")
        if not perms.embed_links:
            return await ctx.send("I need the Embed Links permission in that channel.")
        await self.config.watch_channel.set(channel.id)
        await ctx.send(f"Score updates will be posted to {channel.mention}.")

    @tennisset_watch.command(name="tour")
    async def tennisset_watch_tour(self, ctx: commands.Context, tour: Optional[str] = None):
        """Limit the watch to one tour (atp/wta/challenger/itf/juniors), or `all`."""
        if tour is None:
            current = await self.config.watch_tour()
            return await ctx.send(f"Watching: {current or 'all tours'}.")
        if tour.lower() == "all":
            await self.config.watch_tour.set(None)
            self._watch_prev = None
            return await ctx.send("Watching all tours.")
        try:
            parsed = self._parse_tour(tour)
        except ApiError as e:
            return await ctx.send(e.message)
        await self.config.watch_tour.set(parsed)
        self._watch_prev = None
        await ctx.send(f"Watching {parsed.upper()} only.")

    @tennisset_watch.command(name="interval")
    async def tennisset_watch_interval(self, ctx: commands.Context, seconds: int):
        """Set the poll interval in seconds (minimum 120, default 300).

        Quota math (free tier = 1,000 requests/day, 1 request per tick):
        300s = 288/day, 180s = 480/day, 120s = 720/day.
        """
        if seconds < WATCH_MIN_INTERVAL:
            return await ctx.send(
                f"Minimum interval is {WATCH_MIN_INTERVAL}s "
                "(720 requests/day of the free tier's 1,000/day)."
            )
        await self.config.watch_interval.set(seconds)
        self.watch_loop.change_interval(seconds=seconds)
        note = ""
        if seconds < WATCH_DEFAULT_INTERVAL:
            per_day = 86400 // seconds
            note = (
                f"\n\N{WARNING SIGN} {per_day} requests/day of the free tier's "
                "1,000/day — little headroom left for interactive commands."
            )
        await ctx.send(f"Watch interval set to {seconds}s.{note}")

    @tennisset_watch.command(name="on")
    async def tennisset_watch_on(self, ctx: commands.Context):
        """Enable the score watch."""
        if not await self.config.watch_channel():
            return await ctx.send(
                "Set a channel first with "
                f"`{ctx.clean_prefix}tennisset watch channel #channel`."
            )
        if not await self._get_api_key():
            return await ctx.send(
                "No API key configured. See " f"`{ctx.clean_prefix}tennisset apikey`."
            )
        await self.config.watch_enabled.set(True)
        self._watch_prev = None
        if not self.watch_loop.is_running():
            self.watch_loop.start()
        interval = int(await self.config.watch_interval())
        await ctx.send(
            f"Score watch enabled (every {interval}s, " f"~{86400 // interval} requests/day)."
        )

    @tennisset_watch.command(name="off")
    async def tennisset_watch_off(self, ctx: commands.Context):
        """Disable the score watch."""
        await self._watch_disable()
        await ctx.send("Score watch disabled.")

    @tennisset_watch.command(name="status")
    async def tennisset_watch_status(self, ctx: commands.Context):
        """Show the score watch configuration."""
        enabled = await self.config.watch_enabled()
        cid = await self.config.watch_channel()
        tour = await self.config.watch_tour()
        interval = int(await self.config.watch_interval())
        lines = [
            f"Enabled: {'yes' if enabled else 'no'}"
            + (" (loop running)" if self.watch_loop.is_running() else ""),
            f"Channel: {f'<#{cid}>' if cid else 'not set'}",
            f"Tour: {tour or 'all'}",
            f"Interval: {interval}s (~{86400 // interval} requests/day of the "
            "free tier's 1,000/day)",
        ]
        await ctx.send("\n".join(lines))
