"""LiveTennis - live tennis scores for Red-DiscordBot, powered by livetennisapi.com."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import discord
from redbot.core import commands
from redbot.core.bot import Red

log = logging.getLogger("red.livetennis")

API_BASE = "https://api.livetennisapi.com/api/public/v1"
SERVICE_NAME = "livetennis"
VALID_TOURS = ("atp", "wta", "challenger", "itf", "juniors")
RANKING_SYSTEMS = ("atp", "wta", "itf_jt", "itf_mt", "itf_wt")
UPGRADE_URL = "https://livetennisapi.com/subscribe/upgrade"

# Free tier is 30 req/min & 100/day: cache list responses and never hit the
# API faster than once per 60 seconds for the same query. The cog makes no
# background requests, so quota is only spent when users run commands — but
# on a free key a busy server can still exhaust 100/day; the Basic tier
# (1,000/day) removes that pressure.
CACHE_TTL = 60

COLOR_LIVE = discord.Color.green()
COLOR_UPCOMING = discord.Color.blurple()
COLOR_COMPLETED = discord.Color.dark_grey()
COLOR_ERROR = discord.Color.red()

# Per-feature 403 messages: gated commands degrade into a short upgrade note
# in the same voice, never an error dump.
UPGRADE_MSG = (
    "Completed-match listings need the BASIC tier ($9.99/mo) or any History "
    f"plan — upgrade at <{UPGRADE_URL}>"
)
H2H_UPGRADE_MSG = (
    "Head-to-head needs the BASIC tier ($9.99/mo) or any History plan — "
    f"upgrade at <{UPGRADE_URL}>"
)
RANKINGS_UPGRADE_MSG = (
    f"Rankings listings need the PRO tier ($29.99/mo) — upgrade at <{UPGRADE_URL}>"
)
GENERIC_UPGRADE_MSG = f"This data needs a higher plan — upgrade at <{UPGRADE_URL}>"


# ---------------------------------------------------------------------------
# Pure formatting helpers (kept module-level so they are easy to test)
# ---------------------------------------------------------------------------


def clean_round(tournament: Optional[str], round_: Optional[str]) -> str:
    """Return a display-worthy round name, or "" when the round merely
    restates the tournament name.

    The API's ``round`` field often repeats the tournament (sometimes as
    ``"<Tournament> - <Round>"``). We take the last ``" - "`` segment and
    drop it if it and the tournament contain each other either way.
    """
    if not round_:
        return ""
    seg = round_.split(" - ")[-1].strip()
    if not seg:
        return ""
    t = (tournament or "").strip().lower()
    s = seg.lower()
    if t and (s in t or t in s):
        return ""
    return seg


def player_label(player: Optional[Dict[str, Any]]) -> str:
    """``Name (CC, #rank)`` with every part optional-safe."""
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

    ``games`` is PLAYER-major: ``[[p1 set1, p1 set2, ...], [p2 set1, ...]]``.
    """
    if not score:
        return ""
    games = score.get("games")
    if not games or not isinstance(games, (list, tuple)) or len(games) < 2:
        return ""
    p1, p2 = games[0] or [], games[1] or []
    parts = []
    for a, b in zip(p1, p2):
        if a is None or b is None:
            continue
        parts.append(f"{a}-{b}")
    return " ".join(parts)


def format_points(score: Optional[Dict[str, Any]]) -> str:
    """Current game points as ``(40-AD)``; entries are strings and can be
    null (e.g. ``[null, null]`` on completed matches)."""
    if not score:
        return ""
    pts = score.get("points")
    if (
        isinstance(pts, (list, tuple))
        and len(pts) >= 2
        and pts[0] is not None
        and pts[1] is not None
    ):
        return f"({pts[0]}-{pts[1]})"
    return ""


def format_score_line(match: Dict[str, Any]) -> str:
    """Full score line for a match: per-set games plus live points."""
    score = match.get("score")
    if not score:
        return "Not started"
    games = format_games(score)
    pieces = [p for p in (games, ) if p]
    if (match.get("status") or "").lower() == "live":
        pts = format_points(score)
        if pts:
            pieces.append(pts)
    if not pieces:
        # Fall back to set counts if per-set games are unusable.
        sets = score.get("sets")
        if isinstance(sets, (list, tuple)) and len(sets) >= 2 and None not in sets[:2]:
            return f"Sets {sets[0]}-{sets[1]}"
        return "In progress"
    return " ".join(pieces)


def format_players_line(match: Dict[str, Any]) -> str:
    """``**P1** vs **P2**`` with a serving-ball marker on the server
    (``score.server`` is 1, 2 or null -- roughly 7% null, always guarded)."""
    players = match.get("players") or {}
    p1 = players.get("p1") or {}
    p2 = players.get("p2") or {}
    server = (match.get("score") or {}).get("server")
    winner = match.get("winner")
    s1 = " \N{TENNIS RACQUET AND BALL}" if server == 1 else ""
    s2 = " \N{TENNIS RACQUET AND BALL}" if server == 2 else ""
    w1 = "\N{TROPHY} " if winner == 1 else ""
    w2 = "\N{TROPHY} " if winner == 2 else ""
    line = f"{w1}**{player_label(p1)}**{s1} vs {w2}**{player_label(p2)}**{s2}"
    if match.get("is_doubles"):
        line += " *(doubles)*"
    return line


def discord_timestamp(raw: Any) -> str:
    """ISO instant -> Discord relative timestamp, or "" when unparseable."""
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return f"<t:{int(dt.timestamp())}:R>"
    except (ValueError, OverflowError, OSError):
        return ""


def format_scheduled(match: Dict[str, Any]) -> str:
    """Scheduled time as a Discord relative timestamp, if parseable."""
    raw = match.get("scheduled_time")
    if not raw:
        return ""
    return discord_timestamp(raw) or str(raw)


def match_header(match: Dict[str, Any]) -> str:
    """``Tournament — Round`` with the redundant-round dedupe applied."""
    tournament = match.get("tournament") or "Unknown tournament"
    rnd = clean_round(tournament, match.get("round"))
    return f"{tournament} — {rnd}" if rnd else tournament


def build_match_list_embed(
    matches: List[Dict[str, Any]], *, title: str, color: discord.Color,
    show_schedule: bool = False, footer: str = "",
) -> discord.Embed:
    """One embed listing up to 10 matches, one field per match."""
    embed = discord.Embed(title=title, color=color)
    for m in matches[:10]:
        lines = [format_players_line(m)]
        if show_schedule and not m.get("score"):
            sched = format_scheduled(m)
            if sched:
                lines.append(f"Starts {sched}")
        else:
            lines.append(format_score_line(m))
        lines.append(f"`{m.get('id', '?')}`")
        embed.add_field(name=match_header(m), value="\n".join(lines), inline=False)
    extra = len(matches) - 10
    tail = f"{extra} more match(es) not shown." if extra > 0 else ""
    footer_text = " ".join(x for x in (tail, footer) if x)
    if footer_text:
        embed.set_footer(text=footer_text)
    return embed


def build_match_embed(match: Dict[str, Any]) -> discord.Embed:
    """Detailed embed for a single match."""
    status = (match.get("status") or "unknown").lower()
    color = {
        "live": COLOR_LIVE,
        "upcoming": COLOR_UPCOMING,
        "completed": COLOR_COMPLETED,
    }.get(status, COLOR_UPCOMING)
    embed = discord.Embed(title=match_header(match), color=color)
    embed.add_field(name="Players", value=format_players_line(match), inline=False)
    embed.add_field(name="Status", value=status.title(), inline=True)
    if match.get("score"):
        embed.add_field(name="Score", value=format_score_line(match), inline=True)
    sched = format_scheduled(match)
    if sched:
        embed.add_field(name="Scheduled", value=sched, inline=True)
    if match.get("id"):
        embed.set_footer(text=f"Match ID: {match['id']}")
    return embed


def build_player_embed(player: Dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title=player.get("name") or "Unknown player", color=COLOR_UPCOMING
    )
    if player.get("country"):
        embed.add_field(name="Country", value=str(player["country"]), inline=True)
    if player.get("ranking"):
        embed.add_field(name="Ranking", value=f"#{player['ranking']}", inline=True)
    if player.get("id"):
        embed.set_footer(text=f"Player ID: {player['id']}")
    return embed


def build_h2h_embed(body: Dict[str, Any]) -> discord.Embed:
    """Embed for a ``/h2h`` body whose player fragments both resolved."""
    players = body.get("players") or {}
    n1 = (players.get("p1") or {}).get("name") or "Player 1"
    n2 = (players.get("p2") or {}).get("name") or "Player 2"
    totals = body.get("totals") or {}
    w1 = totals.get("p1_wins") or 0
    w2 = totals.get("p2_wins") or 0
    meetings_total = totals.get("meetings") or 0
    undecided = totals.get("undecided") or 0

    embed = discord.Embed(title=f"{n1} vs {n2}", color=COLOR_UPCOMING)
    if not meetings_total and not undecided:
        embed.description = f"{n1} and {n2} have no recorded meetings."
        return embed
    lines = [f"**{n1} {w1} — {w2} {n2}**"]
    lines.append(f"{meetings_total} meeting(s) with a known winner")
    if undecided:
        lines.append(f"{undecided} further meeting(s) with no derivable winner")
    embed.description = "\n".join(lines)

    surfaces = body.get("by_surface") or {}
    surf_lines = [
        f"{str(surf).title()}: {v.get('p1', 0)}–{v.get('p2', 0)}"
        for surf, v in surfaces.items()
        if isinstance(v, dict)
    ]
    if surf_lines:
        embed.add_field(name="By surface", value="\n".join(surf_lines), inline=False)

    recent = []
    for m in (body.get("meetings") or [])[:5]:
        if not isinstance(m, dict):
            continue
        where = m.get("tournament") or "Unknown tournament"
        line = f"{m.get('date') or '?'} — {where}"
        rnd = clean_round(where, m.get("round"))
        if rnd:
            line += f" ({rnd})"
        tail = []
        winner = {1: n1, 2: n2}.get(m.get("winner"))
        if winner:
            tail.append(f"{winner} won")
        if m.get("score"):
            tail.append(str(m["score"]))
        outcome = str(m.get("outcome") or "").lower()
        if outcome and outcome != "completed":
            tail.append(f"[{outcome}]")
        if tail:
            line += ": " + " ".join(tail)
        recent.append(line)
    if recent:
        embed.add_field(name="Recent meetings", value="\n".join(recent), inline=False)
    embed.set_footer(
        text="Spans the 1968–2022 results archive plus completed matches from 2023 on."
    )
    return embed


def build_rankings_embed(system: str, rows: List[Dict[str, Any]]) -> discord.Embed:
    """Embed for the top of one ``/rankings`` listing (rank-ordered rows)."""
    shown = rows[:10]
    embed = discord.Embed(
        title=f"{system.upper().replace('_', ' ')} rankings — top {len(shown)}",
        color=COLOR_UPCOMING,
    )
    lines = []
    effective = None
    for r in shown:
        rank = r.get("rank")
        line = f"**{rank if rank is not None else '?'}.** {r.get('player_name') or 'Unknown'}"
        points = r.get("points")
        if points is not None:
            line += f" — {points:,} pts"
        prev = r.get("previous_rank")
        if isinstance(prev, int) and isinstance(rank, int) and prev != rank:
            arrow = "\N{BLACK UP-POINTING TRIANGLE}" if prev > rank else "\N{BLACK DOWN-POINTING TRIANGLE}"
            line += f" ({arrow}{abs(prev - rank)})"
        lines.append(line)
        effective = effective or r.get("effective_date")
    embed.description = "\n".join(lines)
    if effective:
        embed.set_footer(text=f"Week of {effective}")
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
    __version__ = "1.1.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        # (path, frozenset(params)) -> (fetched_at_monotonic, payload)
        self._cache: Dict[Tuple[str, frozenset], Tuple[float, Any]] = {}

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        if self.session:
            await self.session.close()

    async def red_delete_data_for_user(self, **kwargs) -> None:
        """This cog stores no end user data."""
        return

    def format_help_for_context(self, ctx: commands.Context) -> str:
        pre = super().format_help_for_context(ctx)
        return f"{pre}\n\nCog Version: {self.__version__}"

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
        upgrade_msg: str = GENERIC_UPGRADE_MSG,
    ) -> Any:
        """GET ``path`` and return the parsed JSON body.

        Raises :class:`ApiError` with a user-facing message on failure.
        List endpoints are cached for 60s to respect the free tier
        (30 requests/min, 100/day).
        """
        assert self.session is not None
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
                raise ApiError(
                    "No Live Tennis API key is configured. The bot owner can set "
                    "one with `[p]tennisset apikey <key>` (or `[p]set api "
                    "livetennis api_key,<key>`). Get a free key at "
                    "<https://livetennisapi.com/subscribe/free>."
                )
            headers["X-API-Key"] = key

        try:
            async with self.session.get(
                f"{API_BASE}{path}",
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 401:
                    raise ApiError(
                        "The configured Live Tennis API key was rejected "
                        "(unauthorized). Please check it and set it again."
                    )
                if resp.status == 403:
                    raise ApiError(upgrade_msg)
                if resp.status == 429:
                    raise ApiError(self._rate_limit_message(
                        await self._error_body(resp),
                        resp.headers.get("Retry-After"),
                    ))
                if resp.status == 404:
                    raise ApiError("Not found.")
                if resp.status == 400:
                    raise ApiError(self._bad_request_message(await self._error_body(resp)))
                if resp.status >= 400:
                    raise ApiError(f"The Live Tennis API returned HTTP {resp.status}.")
                # NOTE: a Retry-After header can also appear on 200 responses;
                # it is advisory noise there and deliberately ignored.
                data = await resp.json()
        except aiohttp.ClientError as exc:
            log.warning("Live Tennis API request failed: %s", exc)
            raise ApiError("Could not reach the Live Tennis API. Try again later.")

        if cache:
            self._cache[cache_key] = (time.monotonic(), data)
        return data

    @staticmethod
    async def _error_body(resp: aiohttp.ClientResponse) -> Dict[str, Any]:
        """Best-effort parse of an error-response body; never raises."""
        try:
            data = await resp.json(content_type=None)
        except (aiohttp.ClientError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _rate_limit_message(body: Dict[str, Any], retry_after: Optional[str]) -> str:
        """User-facing message for the three 429 shapes the API sends:
        per-minute ``rate_limited``, daily ``rate_limited`` (``scope: "day"``,
        with ``resets_at``), and ``abuse_throttled`` (24h block for keys that
        chronically exceed their daily cap; carries ``retry_at_epoch``)."""
        if body.get("error") == "abuse_throttled":
            msg = (
                "This API key is temporarily blocked: it massively exceeded "
                "its daily quota (rejected requests count too). If a script "
                "shares this key, fix its retry loop."
            )
            epoch = body.get("retry_at_epoch")
            if isinstance(epoch, (int, float)):
                msg += f" Access resumes <t:{int(epoch)}:R>."
            return msg
        if body.get("scope") == "day":
            msg = "Daily request quota reached"
            limit = body.get("limit_per_day")
            if limit:
                msg += f" ({limit}/day on this plan)"
            msg += "."
            ts = discord_timestamp(body.get("resets_at"))
            if ts:
                msg += f" It resets {ts}."
            msg += f" A higher tier raises the cap: <{UPGRADE_URL}>"
            return msg
        wait = f" Try again in {retry_after}s." if retry_after else ""
        return f"Rate limited by the Live Tennis API.{wait}"

    @staticmethod
    def _bad_request_message(body: Dict[str, Any]) -> str:
        """User-facing message for a 400, surfacing ``ambiguous_name``
        candidate lists (/h2h refuses fragments matching several players)."""
        if body.get("error") == "ambiguous_name":
            candidates = body.get("candidates")
            names = ""
            if isinstance(candidates, list):
                names = ", ".join(str(c) for c in candidates[:8])
            msg = "That name matches more than one player"
            if names:
                msg += f": {names}"
            return msg + ". Please use a more specific name."
        detail = body.get("detail")
        if detail:
            return f"The Live Tennis API rejected the request: {detail}"
        return "The Live Tennis API rejected the request (HTTP 400)."

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        """List endpoints wrap results as ``{"data": [...], "meta": {...}}``;
        detail endpoints (``/matches/{id}``, ``/players/{id}``) return the
        object at the top level. Accept both."""
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    @staticmethod
    def _parse_tour(tour: Optional[str]) -> Optional[str]:
        if tour is None:
            return None
        tour = tour.lower().strip()
        if tour not in VALID_TOURS:
            raise ApiError(
                f"Unknown tour `{tour}`. Valid tours: {', '.join(VALID_TOURS)}."
            )
        return tour

    async def _matches(self, status: str, tour: Optional[str], limit: int = 25) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"status": status, "limit": limit}
        t = self._parse_tour(tour)
        if t:
            params["tour"] = t
        # Only ?status=completed is gated (BASIC+ or any History plan).
        payload = await self._request("/matches", params, cache=True, upgrade_msg=UPGRADE_MSG)
        return self._unwrap(payload) or []

    # -- commands: tennis ---------------------------------------------------

    @commands.group()
    async def tennis(self, ctx: commands.Context):
        """Live tennis scores from livetennisapi.com."""

    @tennis.command(name="live")
    @commands.cooldown(1, 5, commands.BucketType.channel)
    async def tennis_live(self, ctx: commands.Context, tour: Optional[str] = None):
        """Show live matches, optionally for one tour.

        Tours: atp, wta, challenger, itf, juniors.
        Results are cached for 60 seconds.
        """
        async with ctx.typing():
            try:
                matches = await self._matches("live", tour)
            except ApiError as e:
                return await ctx.send(embed=discord.Embed(description=e.message, color=COLOR_ERROR))
            if not matches:
                return await ctx.send("No live matches right now.")
            title = f"Live matches — {tour.upper()}" if tour else "Live matches"
            embed = build_match_list_embed(
                matches, title=title, color=COLOR_LIVE,
                footer="Scores refresh at most once per 60s.",
            )
        await ctx.send(embed=embed)

    @tennis.command(name="upcoming")
    @commands.cooldown(1, 5, commands.BucketType.channel)
    async def tennis_upcoming(self, ctx: commands.Context, tour: Optional[str] = None):
        """Show upcoming matches, optionally for one tour."""
        async with ctx.typing():
            try:
                matches = await self._matches("upcoming", tour)
            except ApiError as e:
                return await ctx.send(embed=discord.Embed(description=e.message, color=COLOR_ERROR))
            if not matches:
                return await ctx.send("No upcoming matches found.")
            title = f"Upcoming matches — {tour.upper()}" if tour else "Upcoming matches"
            embed = build_match_list_embed(
                matches, title=title, color=COLOR_UPCOMING, show_schedule=True
            )
        await ctx.send(embed=embed)

    @tennis.command(name="results")
    @commands.cooldown(1, 5, commands.BucketType.channel)
    async def tennis_results(self, ctx: commands.Context, tour: Optional[str] = None):
        """Show recently completed matches, optionally for one tour."""
        async with ctx.typing():
            try:
                matches = await self._matches("completed", tour)
            except ApiError as e:
                return await ctx.send(embed=discord.Embed(description=e.message, color=COLOR_ERROR))
            if not matches:
                return await ctx.send("No completed matches found.")
            title = f"Results — {tour.upper()}" if tour else "Results"
            embed = build_match_list_embed(matches, title=title, color=COLOR_COMPLETED)
        await ctx.send(embed=embed)

    @tennis.command(name="match")
    @commands.cooldown(1, 5, commands.BucketType.channel)
    async def tennis_match(self, ctx: commands.Context, match_id: str):
        """Show one match in detail by its ID."""
        async with ctx.typing():
            try:
                payload = await self._request(f"/matches/{match_id}")
            except ApiError as e:
                return await ctx.send(embed=discord.Embed(description=e.message, color=COLOR_ERROR))
            match = self._unwrap(payload)
            if not match or not isinstance(match, dict):
                return await ctx.send("Match not found.")
        await ctx.send(embed=build_match_embed(match))

    @tennis.command(name="player")
    @commands.cooldown(1, 5, commands.BucketType.channel)
    async def tennis_player(self, ctx: commands.Context, *, name: str):
        """Search for a player by name."""
        async with ctx.typing():
            try:
                payload = await self._request("/players", {"search": name}, cache=True)
            except ApiError as e:
                return await ctx.send(embed=discord.Embed(description=e.message, color=COLOR_ERROR))
            players = self._unwrap(payload) or []
            if not players:
                return await ctx.send(f"No players found matching `{name}`.")
            if len(players) == 1:
                return await ctx.send(embed=build_player_embed(players[0]))
            embed = discord.Embed(
                title=f"Players matching “{name}”", color=COLOR_UPCOMING
            )
            lines = []
            for p in players[:10]:
                lines.append(f"{player_label(p)} — `{p.get('id', '?')}`")
            embed.description = "\n".join(lines)
            if len(players) > 10:
                embed.set_footer(text=f"{len(players) - 10} more not shown.")
        await ctx.send(embed=embed)

    @tennis.command(name="h2h")
    @commands.cooldown(1, 5, commands.BucketType.channel)
    async def tennis_h2h(self, ctx: commands.Context, *, players: str):
        """Head-to-head record between two players.

        Separate the two names with `vs`:
        `[p]tennis h2h federer vs nadal`

        Spans the 1968–2022 results archive plus completed matches from
        2023 on. Needs the BASIC tier ($9.99/mo) or any History plan; on a
        free key the bot replies with an upgrade note.
        """
        parts = [
            p.strip()
            for p in re.split(r"\s+vs\.?\s+", players, flags=re.IGNORECASE)
            if p.strip()
        ]
        if len(parts) != 2:
            return await ctx.send(
                "Give me two names separated by `vs`, e.g. "
                "`federer vs nadal`."
            )
        if any(len(p) < 3 for p in parts):
            return await ctx.send("Each name needs at least 3 characters.")
        p1, p2 = parts
        async with ctx.typing():
            try:
                body = await self._request(
                    "/h2h", {"p1": p1, "p2": p2},
                    cache=True, upgrade_msg=H2H_UPGRADE_MSG,
                )
            except ApiError as e:
                return await ctx.send(embed=discord.Embed(description=e.message, color=COLOR_ERROR))
            if not isinstance(body, dict) or not body.get("players"):
                # 200 with null players: no player matches the fragments.
                return await ctx.send(
                    f"No player matches `{p1}` and/or `{p2}`."
                )
            embed = build_h2h_embed(body)
        await ctx.send(embed=embed)

    @tennis.command(name="rankings")
    @commands.cooldown(1, 5, commands.BucketType.channel)
    async def tennis_rankings(self, ctx: commands.Context, system: str = "atp"):
        """Top 10 of an official ranking table.

        Systems: atp, wta, itf_jt, itf_mt, itf_wt.
        Needs the PRO tier ($29.99/mo); on a lower tier the bot replies
        with an upgrade note.
        """
        system = system.lower().strip()
        if system not in RANKING_SYSTEMS:
            return await ctx.send(
                f"Unknown system `{system}`. Valid systems: "
                f"{', '.join(RANKING_SYSTEMS)}."
            )
        async with ctx.typing():
            try:
                payload = await self._request(
                    "/rankings", {"system": system, "limit": 10},
                    cache=True, upgrade_msg=RANKINGS_UPGRADE_MSG,
                )
            except ApiError as e:
                return await ctx.send(embed=discord.Embed(description=e.message, color=COLOR_ERROR))
            rows = self._unwrap(payload) or []
            if not rows:
                return await ctx.send("No ranking rows returned.")
            embed = build_rankings_embed(system, rows)
        await ctx.send(embed=embed)

    @tennis.command(name="status")
    @commands.cooldown(1, 10, commands.BucketType.guild)
    async def tennis_status(self, ctx: commands.Context):
        """Check whether the Live Tennis API is reachable."""
        async with ctx.typing():
            try:
                await self._request("/health", needs_key=False)
            except ApiError as e:
                return await ctx.send(embed=discord.Embed(description=f"API health check failed: {e.message}", color=COLOR_ERROR))
        await ctx.send("\N{WHITE HEAVY CHECK MARK} The Live Tennis API is up.")

    # -- commands: tennisset ------------------------------------------------

    @commands.group()
    @commands.is_owner()
    async def tennisset(self, ctx: commands.Context):
        """Configure the LiveTennis cog (bot owner only)."""

    @tennisset.command(name="apikey")
    async def tennisset_apikey(self, ctx: commands.Context, key: Optional[str] = None):
        """Set the Live Tennis API key (stored globally via Red's shared API tokens).

        Prefer running this in DM. You can also use the standard
        `[p]set api livetennis api_key,<key>`.
        Get a free key at <https://livetennisapi.com/subscribe/free>.
        """
        if key is None:
            return await ctx.send_help()
        if ctx.guild is not None:
            try:
                await ctx.message.delete()
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
        await self.bot.set_shared_api_tokens(SERVICE_NAME, api_key=key)
        self._cache.clear()
        await ctx.send(
            "Live Tennis API key saved. "
            "If you posted it in a public channel, consider rotating it."
        )
