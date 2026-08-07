# livetennis-redbot

[![ci](https://github.com/livetennisapi/livetennis-redbot/actions/workflows/ci.yml/badge.svg)](https://github.com/livetennisapi/livetennis-redbot/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Cogs for [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot) by
[Live Tennis API](https://livetennisapi.com) — live tennis scores from ATP,
WTA, Challenger, ITF and juniors in Discord embeds.

## Installation

```
[p]repo add livetennis-redbot https://github.com/livetennisapi/livetennis-redbot
[p]cog install livetennis-redbot livetennis
[p]load livetennis
```

`[p]` is your bot's prefix.

## Quickstart

The bot owner needs a Live Tennis API key (free tier available at
<https://livetennisapi.com/subscribe/free>):

```
[p]tennisset apikey twjp_your_key_here
```

or the standard Red way:

```
[p]set api livetennis api_key,twjp_your_key_here
```

Set the key in a DM with the bot so it never appears in a public channel.
Then try:

```
[p]tennis live
```

## Commands

| Command | Tier | Description |
|---|---|---|
| `[p]tennis live [tour]` | FREE | Live matches with per-set scores, live game points and a serving indicator |
| `[p]tennis upcoming [tour]` | FREE | Upcoming matches with start times |
| `[p]tennis results [tour]` | BASIC+ or any History plan | Recently completed matches |
| `[p]tennis match <id>` | FREE | Detail for one match (IDs are shown in list output) |
| `[p]tennis player <name>` | FREE | Player search (name, country, ranking) |
| `[p]tennis h2h <p1> vs <p2>` | BASIC+ or any History plan | Head-to-head record between two players, spanning the 1968–2022 results archive plus completed matches from 2023 on |
| `[p]tennis rankings [system]` | PRO | Top 10 of an official ranking table (`atp`, `wta`, `itf_jt`, `itf_mt`, `itf_wt`) |
| `[p]tennis status` | — | API health check (no key needed) |
| `[p]tennisset apikey <key>` | — | Set the API key (owner only) |

`tour` is one of `atp`, `wta`, `challenger`, `itf`, `juniors`.

Gated commands degrade gracefully: on a lower tier the bot replies with a
short upgrade note instead of an error dump.

## Quotas

| Tier | Requests/min | Requests/day | Price |
|---|---|---|---|
| FREE | 30 | 100 | $0 |
| BASIC | 60 | 1,000 | $9.99/mo |
| PRO | 300 | 10,000 | $29.99/mo |
| ULTRA | 600 | 500,000 | $99.99/mo |

The free API tier allows 30 requests/minute and 100/day. The cog is entirely
query-driven — it makes **no** background requests — and caches list responses
for 60 seconds, so identical queries within a minute cost nothing. There is
deliberately no auto-posting/score-follow loop. Note that 100/day is easy to
exhaust on an active server (roughly 100 distinct commands across the whole
day); for busy servers the BASIC tier ($9.99/mo, 1,000/day) is recommended.
If a key gets throttled for chronically exceeding its daily cap, the bot says
so and shows when access resumes.

## Authentication

The owner's key (prefix `twjp_`) is stored via Red's shared API token storage
and sent to the API as an `X-API-Key` header. For your own scripts against the
same API, `Authorization: Bearer twjp_...` is the preferred form, and `?token=`
exists for header-less WebSocket clients.

## End user data

This cog stores no end user data. The API key is stored via Red's shared API
token storage.

## Links

- Docs: <https://docs.livetennisapi.com>
- Free API key: <https://livetennisapi.com/subscribe/free>
- Discord: <https://discord.gg/f8WUZHgDm6>
- GitHub org: <https://github.com/livetennisapi>

## License

MIT — see [LICENSE](LICENSE).

## Affiliate program

Know developers who need tennis data? The [affiliate program](https://affiliates.livetennisapi.com/program) pays 51% recurring commission for the life of every referred subscription — 30-day cookie, and the people you refer get 10% off.
