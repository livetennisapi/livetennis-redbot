"""A stand-in for api.livetennisapi.com.

Serves payloads shaped to the published OpenAPI schema (docs.livetennisapi.com/
openapi.yaml) so the tests exercise the real aiohttp/JSON/status-code path
without a network connection and without any API key.

Key vocabulary used by the tests:
    good-key    -> 200 with data
    empty-key   -> 200 with empty lists
    bad-key     -> 401 {"error": "unauthorized"}   (matches the live API)
    basic-key   -> 403 on tier-gated endpoints
    burst-key   -> 429 with Retry-After
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PREFIX = "/api/public/v1"

MATCH_LIVE = {
    "id": 18042,
    "tournament": "Wimbledon",
    "surface": "grass",
    "indoor": False,
    "format": "BO5",
    "round": "Wimbledon - Final",  # deliberately redundant: exercises clean_round
    "status": "live",
    "event_status": "In Progress",
    "is_doubles": False,
    "scheduled_time": "2026-07-18T13:00:00Z",
    "players": {
        "p1": {"id": 1, "name": "Jannik Sinner", "country": "ITA", "ranking": 1},
        "p2": {"id": 2, "name": "Carlos Alcaraz", "country": "ESP", "ranking": 2},
    },
    "score": {
        "sets": [1, 1],
        "games": [[6, 3], [4, 5]],
        "points": ["30", "40"],
        "server": 1,
        "is_tiebreak": False,
        "timestamp": "2026-07-18T15:10:00Z",
    },
    "winner": None,
}

# A match whose score is entirely null/empty -- the schema explicitly warns that
# completed matches are observed live carrying null points and empty games.
MATCH_RAGGED = {
    "id": 18043,
    "tournament": "ATP Umag",
    "surface": None,
    "indoor": False,
    "format": None,
    "round": None,
    "status": "live",
    "is_doubles": True,
    "scheduled_time": None,
    "players": {
        "p1": {"id": None, "name": "Doubles Team A", "country": None, "ranking": None},
        "p2": {"name": "Doubles Team B"},
    },
    "score": {"sets": [0, 0], "games": [[], []], "points": [None, None], "server": None},
    "winner": None,
}

MATCH_UPCOMING = {
    "id": 18100,
    "tournament": "US Open",
    "surface": "hard",
    "indoor": False,
    "format": "BO5",
    "round": "R32",
    "status": "upcoming",
    "is_doubles": False,
    "scheduled_time": "2026-08-30T18:30:00Z",
    "players": {
        "p1": {"id": 5, "name": "Novak Djokovic", "country": "SRB", "ranking": 5},
        "p2": {"id": 6, "name": "Alexander Zverev", "country": "GER", "ranking": 6},
    },
    "score": None,
    "winner": None,
}

MATCH_COMPLETED = {
    "id": 17900,
    "tournament": "Roland Garros",
    "round": "SF",
    "status": "completed",
    "is_doubles": False,
    "scheduled_time": "2026-06-05T11:00:00Z",
    "players": {
        "p1": {"id": 3, "name": "Iga Swiatek", "country": "POL", "ranking": 1},
        "p2": {"id": 4, "name": "Coco Gauff", "country": "USA", "ranking": 2},
    },
    "score": {"sets": [2, 0], "games": [[6, 6], [3, 2]], "points": [None, None], "server": None},
    "winner": 1,
}

SCORE = {
    "sets": [1, 1],
    "games": [[6, 3], [4, 5]],
    "points": ["30", "40"],
    "server": 1,
    "is_tiebreak": False,
    "win_probability_p1": 0.62,
    "timestamp": "2026-07-18T15:10:00Z",
}

PLAYER_FULL = {
    "id": 1,
    "name": "Jannik Sinner",
    "tour": "atp",
    "country": "ITA",
    "ranking": 1,
    "ranking_points": 11500,
    "ranking_movement": "same",
    "hand": "R",
    "backhand": 2,
    "birthday": "2001-08-16",
    "is_doubles_team": False,
    "stats": {"ratings": {}, "season": []},
}

PLAYER_LIST = [
    PLAYER_FULL,
    {"id": 7, "name": "Janko Tipsarevic", "country": "SRB", "ranking": None},
    {"id": 8, "name": "Jan-Lennard Struff", "country": "GER", "ranking": 45},
]

FIXTURES = [
    {
        "id": 20001,
        "event_date": "2026-08-30",
        "tour": "atp",
        "tournament": "US Open",
        "round": "R64",
        "surface": "hard",
        "player1_name": "Taylor Fritz",
        "player2_name": "Ben Shelton",
        "status": "scheduled",
    },
    {
        "id": 20002,
        "event_date": None,
        "tour": None,
        "tournament": None,
        "round": None,
        "surface": None,
        "player1_name": None,  # unresolved draw slot
        "player2_name": "Qualifier",
        "status": None,
    },
]


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (stdlib API)
        url = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        if not path.startswith(PREFIX):
            return self._send(404, {"error": "not found"})
        path = path[len(PREFIX) :]
        self.server.requests.append((path, qs, self.headers.get("x-api-key")))

        if path == "/health":  # public, no auth
            return self._send(200, {"status": "ok", "version": "v1"})

        key = self.headers.get("x-api-key")
        if not key or key == "bad-key":
            return self._send(401, {"error": "unauthorized"})
        if key == "burst-key":
            return self._send(429, {"error": "rate limited"}, extra={"Retry-After": "30"})

        empty = key == "empty-key"

        if path == "/matches":
            status = qs.get("status", "live")
            if status == "completed" and key != "pro-key":
                return self._send(403, {"error": "tier"})
            if empty:
                return self._send(200, {"data": [], "meta": {"count": 0}})
            table = {
                "live": [MATCH_LIVE, MATCH_RAGGED],
                "upcoming": [MATCH_UPCOMING],
                "completed": [MATCH_COMPLETED],
            }
            data = table.get(status, [])
            return self._send(200, {"data": data, "meta": {"count": len(data)}})

        if path == "/fixtures":
            data = [] if empty else FIXTURES
            return self._send(200, {"data": data, "meta": {"count": len(data)}})

        if path == "/players":
            data = [] if empty else PLAYER_LIST
            if qs.get("search") == "sinner":
                data = [PLAYER_FULL]
            return self._send(200, {"data": data, "meta": {"count": len(data)}})

        if path.startswith("/players/"):
            pid = path.rsplit("/", 1)[-1]
            if pid != "1":
                return self._send(404, {"error": "not found"})
            return self._send(200, PLAYER_FULL)

        if path.endswith("/score"):
            mid = path.split("/")[2]
            if mid != "18042":
                return self._send(404, {"error": "not found"})
            return self._send(200, SCORE)

        if path.startswith("/matches/"):
            mid = path.rsplit("/", 1)[-1]
            if mid != "18042":
                return self._send(404, {"error": "not found"})
            return self._send(200, MATCH_LIVE)

        return self._send(404, {"error": "not found"})

    def _send(self, code, body, extra=None):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # silence stderr spam
        pass


class MockAPI:
    """Threaded mock server; ``base_url`` is what the cog should point at."""

    def __init__(self):
        self._srv = HTTPServer(("127.0.0.1", 0), _Handler)
        self._srv.requests = []
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
        self._thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        host, port = self._srv.server_address[:2]
        return f"http://{host}:{port}{PREFIX}"

    @property
    def requests(self):
        return self._srv.requests
