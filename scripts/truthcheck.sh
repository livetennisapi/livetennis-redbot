#!/bin/sh
# truthcheck.sh — pin product facts to ground truth.
# Fails when stale quota numbers, wrong URLs or forbidden identities appear
# in tracked files. CHANGELOG.md is exempt (historical entries may describe
# old numbers), as is this script itself (it must name the forbidden strings).
set -u
cd "$(dirname "$0")/.."

FILES=$(git ls-files | grep -Ev '^(CHANGELOG\.md|scripts/truthcheck\.sh)$')
status=0

forbid() { # $1 = pattern, $2 = reason
    hits=$(printf '%s\n' "$FILES" | xargs grep -riEn "$1" 2>/dev/null)
    if [ -n "$hits" ]; then
        echo "truthcheck FORBIDDEN ($2):"
        echo "$hits"
        status=1
    fi
}

# 1. Stale 100k/day free quota.
forbid '100[,.]?000[^0-9].{0,40}(/ ?day|per ?day|daily)|100k.{0,30}(/ ?day|per ?day|daily)' 'stale 100,000/day quota — FREE is 100/day'
# 2. Free tier paired with 1,000/day (that is BASIC).
forbid 'free.{0,80}1[,.]?000 ?(requests?)? ?(/ ?day|per ?day)' 'free tier is 100/day, not 1,000'
# 3. Wrong docs URL.
forbid 'livetennisapi\.com/docs' 'docs live at docs.livetennisapi.com'
# 4. Personal identity in repo content.
forbid 'bensynapse' 'use the org identity'
# 5. Wrong daily-reset story.
forbid 'midnight UTC' 'daily reset is a local-midnight-derived instant, not midnight UTC'

# If the repo states quotas at all, the current free cap and the docs URL
# must both be present somewhere.
if printf '%s\n' "$FILES" | xargs grep -riE 'requests?/(min|day)|per day|/day' >/dev/null 2>&1; then
    if ! printf '%s\n' "$FILES" | xargs grep -rE '100( requests)?/day' >/dev/null 2>&1; then
        echo 'truthcheck MISSING: current free quota "100/day"'
        status=1
    fi
    if ! printf '%s\n' "$FILES" | xargs grep -r 'docs\.livetennisapi\.com' >/dev/null 2>&1; then
        echo 'truthcheck MISSING: docs.livetennisapi.com link'
        status=1
    fi
fi

if [ "$status" -eq 0 ]; then
    echo 'truthcheck: OK'
fi
exit "$status"
