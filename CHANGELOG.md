# Changelog

All notable changes to the LiveTennis cog are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-08-07

First versioned release: the cog version now lives in `livetennis/info.json`
and `LiveTennis.__version__` (shown in `[p]help`), and releases are tagged.

### Added
- `[p]tennis h2h <p1> vs <p2>` — head-to-head record between two players
  across the 1968–2022 results archive plus completed matches from 2023 on:
  win totals, undecided meetings, per-surface split and the five most recent
  meetings. BASIC tier or any History plan; on a free key the bot replies
  with an upgrade note. Ambiguous name fragments show the API's candidate
  list instead of a bare error.
- `[p]tennis rankings [system]` — top 10 of an official ranking table
  (`atp`, `wta`, `itf_jt`, `itf_mt`, `itf_wt`) with points and weekly
  movement. PRO tier; below PRO the bot replies with an upgrade note.
- `scripts/truthcheck.sh` and a CI step pinning quota numbers and URLs to
  the current product facts.

### Changed
- 429 handling now reads the response body: an `abuse_throttled` block
  (a key chronically over its daily cap) explains the block, advises fixing
  client retry loops and shows when access resumes as a Discord timestamp;
  a daily-cap 429 shows the plan's daily limit and the reset time. Plain
  per-minute 429s keep the Retry-After message.
- 403 upgrade notes are per-feature instead of one message for everything.
- 400 responses now surface the API's `detail` text.
- README rebuilt: tier-gated command table, current quota grid, auth notes
  and a links block.

## Pre-versioning history

The cog shipped unversioned before 1.1.0. Notable changes:

- 2026-08-06 — quota copy updated to the grid effective 2026-08-06:
  FREE 100/day, BASIC 1,000/day, PRO 10,000/day.
- 2026-08-02 — initial LiveTennis cog: `live`, `upcoming`, `results`,
  `match`, `player` and `status` commands, 60-second response cache,
  no background polling; CI byte-compile + info.json validation.
