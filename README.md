# livetennis-redbot

Cogs for [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot) by
[Live Tennis API](https://livetennisapi.com).

## Installation

```
[p]repo add livetennis-redbot https://github.com/livetennisapi/livetennis-redbot
[p]cog install livetennis-redbot livetennis
[p]load livetennis
```

`[p]` is your bot's prefix.

## LiveTennis cog

Live ATP / WTA / Challenger / ITF / Juniors tennis scores in Discord embeds.

### Setup

The bot owner needs a Live Tennis API key (free tier available at
<https://livetennisapi.com/subscribe/free>):

```
[p]tennisset apikey <key>
```

or the standard Red way:

```
[p]set api livetennis api_key,<key>
```

Set the key in a DM with the bot so it never appears in a public channel.

### Commands

| Command | Description |
|---|---|
| `[p]tennis live [tour]` | Live matches with per-set scores, live game points and a serving indicator |
| `[p]tennis upcoming [tour]` | Upcoming matches with start times |
| `[p]tennis results [tour]` | Recently completed matches — needs the BASIC tier ($9.99/mo) or any History plan; on a free key the bot replies with an upgrade note |
| `[p]tennis match <id>` | Detail for one match (IDs are shown in list output) |
| `[p]tennis player <name>` | Player search (name, country, ranking) |
| `[p]tennis status` | API health check |
| `[p]tennisset apikey <key>` | Set the API key (owner only) |

`tour` is one of `atp`, `wta`, `challenger`, `itf`, `juniors`.

### Rate limits

The free API tier allows 30 requests/minute and 1000/day. The cog is entirely
query-driven — it makes **no** background requests — and caches list responses
for 60 seconds, so identical queries within a minute cost nothing. There is
deliberately no auto-posting/score-follow loop.

### End user data

This cog stores no end user data. The API key is stored via Red's shared API
token storage.

## License

MIT — see [LICENSE](LICENSE).
