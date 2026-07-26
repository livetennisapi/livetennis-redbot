# livetennis-redbot

Cogs for [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot) published by
[Live Tennis API](https://livetennisapi.com) — we are the vendor of the API this cog
talks to.

## Installation

```
[p]repo add livetennis-redbot https://github.com/livetennisapi/livetennis-redbot
[p]cog install livetennis-redbot livetennis
[p]load livetennis
```

`[p]` is your bot's prefix.

## LiveTennis cog

Live ATP / WTA / Challenger / ITF / Juniors tennis in Discord embeds.

### Setup

The bot owner needs a Live Tennis API key. The free tier (30 requests/minute,
1,000/day) is available at <https://livetennisapi.com/subscribe/free>.

The key is stored with **Red's shared API token store**, so it is set through
Red's own command rather than being passed to this cog:

```
[p]set api livetennis api_key <your key>
```

Run `[p]set api` with no arguments for Red's interactive flow, or send the
command in a DM, so the key never appears in a channel. `[p]tennisset apikey`
prints these instructions and tells you whether a key is currently set.

### Commands

| Command | Description |
|---|---|
| `[p]tennis live [tour]` | Live matches with per-set scores, live game points and a serving indicator |
| `[p]tennis upcoming [tour]` | Upcoming matches with start times |
| `[p]tennis fixtures [tour] [limit]` | Scheduled fixture list, earliest first (name-only players) |
| `[p]tennis results [tour]` | Recently completed matches (**needs a Basic-tier key or higher**) |
| `[p]tennis match <id>` | Full detail for one match (IDs are shown in list output) |
| `[p]tennis score <id>` | Score only for one match — the API's lowest-latency read |
| `[p]tennis player <name or id>` | Player search by name, or a full profile by numeric player ID |
| `[p]tennis status` | API health check (no key required) |
| `[p]tennisset apikey` | How to set the API key, and whether one is set (owner only) |
| `[p]tennisset watch ...` | Configure the optional score watch (owner only) |

`tour` is one of `atp`, `wta`, `challenger`, `itf`, `juniors`. An unknown tour is
rejected locally, so it never costs an API request.

The cog needs the **Embed Links** permission in any channel where the embed
commands are used.

### Score watch (optional, off by default)

The bot owner can have the cog follow the live feed and post match
started / set-score / finished updates to one channel:

```
[p]tennisset watch channel #tennis
[p]tennisset watch tour atp        (or: all)
[p]tennisset watch interval 300
[p]tennisset watch on
```

Each tick costs exactly **one** API request (the live-match list), so
requests/day = 86400 / interval: **300 s (default) = 288/day**, and the
enforced floor of **120 s = 720/day**, against the free tier's 1,000/day —
setting an interval below 300 s prints a quota warning. Finished matches are
announced from the last seen live score; the watcher never spends extra
requests on match-detail lookups. If the API key is missing/rejected or the
channel disappears, the watch disables itself instead of burning quota.

### Rate limits and tiers

The free API tier allows 30 requests/minute and 1,000/day. Interactive commands
are query-driven and list responses are cached for 60 seconds, so identical
queries inside that window cost nothing. The only background traffic is the
opt-in score watch documented above.

`[p]tennis live`, `upcoming`, `fixtures`, `match`, `score`, `player` and
`status` are all free-tier endpoints. `[p]tennis results` uses
`status=completed`, which is a Basic-tier endpoint — on a free key the API
answers HTTP 403 and the cog reports that a higher tier is needed.

### Errors

Every API failure is turned into a plain-language message: a missing key points
at `[p]set api`, a rejected key (401) says so, a tier-gated endpoint (403)
points at the plans page, a rate limit (429) reports `Retry-After` when the API
sends one, and timeouts / connection failures ask the user to retry.

### End user data

This cog stores no end user data. The API key is stored via Red's shared API
token storage; score-watch settings (channel id, tour filter, interval) are
stored via Red's Config.

## Development

```
python -m pip install -r requirements-dev.txt   # or: pip install pytest pytest-asyncio Red-DiscordBot
python -m pytest
```

The test suite loads the cog into a real `Red` instance through Red's own
extension loader and exercises every command against a stub HTTP layer — no API
key and no network access are needed to run it.

## AI assistance

Parts of this repository were written with AI assistance (Anthropic Claude) and
reviewed by a human maintainer before release.

## License

MIT — see [LICENSE](LICENSE).
