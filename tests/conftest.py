import sys
from pathlib import Path

import pytest

# Make the repo root importable so `livetennis` resolves without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Red ships pytest fixtures (including a real `Red` instance) for cog authors.
pytest_plugins = ["redbot.pytest.core"]

from .mock_api import MockAPI  # noqa: E402


@pytest.fixture()
def mock_api():
    """A local stand-in for api.livetennisapi.com (no key, no network)."""
    with MockAPI() as api:
        yield api
