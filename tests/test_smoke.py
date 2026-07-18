"""Smoke tests for batru-mcp.

Offline tests (default) cover the hero name -> shortName/id normalisation logic
and the tools' draft assembly without touching the network. Live tests (run with
`uv run pytest -m live`) call the real batru.gg API.
"""

from __future__ import annotations

import os

import pytest

from batru_mcp import server
from batru_mcp.client import BatruClient
from batru_mcp.hero_lookup import HeroResolver

# Minimal fixtures shaped like the real endpoints.
DOTA_HEROES = [
    {"id": 1, "name": "npc_dota_hero_antimage", "displayName": "Anti-Mage",
     "shortName": "antimage", "aliases": ["am", "wei"]},
    {"id": 2, "name": "npc_dota_hero_axe", "displayName": "Axe",
     "shortName": "axe", "aliases": []},
    {"id": 5, "name": "npc_dota_hero_crystal_maiden", "displayName": "Crystal Maiden",
     "shortName": "crystal_maiden", "aliases": ["cm", "rylai"]},
]
DEADLOCK_HEROES = [
    {"id": 1, "name": "Infernus"},
    {"id": 4, "name": "Lady Geist"},
    {"id": 18, "name": "Mo & Krill"},
]


# --- hero resolution (offline) --------------------------------------------

def test_dota_resolve_exact_and_alias():
    r = HeroResolver.from_dota(DOTA_HEROES)
    assert r.resolve("antimage").short_name == "antimage"
    assert r.resolve("am").short_name == "antimage"           # alias
    assert r.resolve("Anti-Mage").short_name == "antimage"    # display, punctuation
    assert r.resolve("ANTI MAGE").short_name == "antimage"    # case + space
    assert r.resolve("cm").short_name == "crystal_maiden"     # alias


def test_dota_resolve_unknown_suggests():
    r = HeroResolver.from_dota(DOTA_HEROES)
    assert r.resolve("antimagee") is None                     # exact fails
    suggestions = r.suggest("antimagee")
    assert "Anti-Mage" in suggestions


def test_deadlock_resolve_to_id():
    r = HeroResolver.from_deadlock(DEADLOCK_HEROES)
    assert r.resolve("infernus").id == 1
    assert r.resolve("Lady Geist").id == 4
    assert r.resolve("mo  krill").id == 18                    # punctuation collapsed


# --- tool draft assembly (offline, stubbed client) ------------------------

class _StubClient:
    """Stands in for BatruClient: pre-built resolvers, captured payloads."""

    def __init__(self):
        self._dr = HeroResolver.from_dota(DOTA_HEROES)
        self.last_winrate = None
        self.last_recommend = None

    def dota_resolver(self):
        return self._dr

    def winrate(self, radiant, dire):
        self.last_winrate = (radiant, dire)
        return {"radiant_win_rate": 56.93, "dire_win_rate": 43.07}

    def recommend(self, radiant, dire, is_our_team_dire):
        self.last_recommend = (radiant, dire, is_our_team_dire)
        return [{"hero_key": "axe", "name": "Axe", "win_rate": 55.0, "next_picks": None}]


@pytest.fixture
def stub(monkeypatch):
    s = _StubClient()
    monkeypatch.setattr(server, "_client", s)
    return s


def test_predict_winrate_normalizes_to_shortnames(stub):
    out = server.predict_dota_winrate(["am"], ["cm"], my_side="radiant")
    assert stub.last_winrate == (["antimage"], ["crystal_maiden"])
    assert out["my_win_rate_pct"] == 56.93
    assert out["enemy_win_rate_pct"] == 43.07


def test_predict_winrate_dire_side_swaps(stub):
    out = server.predict_dota_winrate(["am"], ["cm"], my_side="dire")
    # my_heroes on dire -> radiant gets the enemy, dire gets us.
    assert stub.last_winrate == (["crystal_maiden"], ["antimage"])
    assert out["my_win_rate_pct"] == 43.07     # dire side rate


def test_predict_winrate_unknown_hero_returns_candidates(stub):
    out = server.predict_dota_winrate(["antimagee"], ["axe"])
    assert "error" in out
    assert "Anti-Mage" in out["did_you_mean"]


def test_recommend_normalizes_and_maps(stub):
    out = server.recommend_dota_pick(["am"], ["cm"], my_side="radiant")
    assert stub.last_recommend == (["antimage"], ["crystal_maiden"], False)
    assert out["recommendations"][0]["shortName"] == "axe"
    assert out["recommendations"][0]["win_rate_pct"] == 55.0


# --- live tests (opt-in: uv run pytest -m live) ---------------------------

@pytest.fixture(scope="module")
def live_client():
    return BatruClient(os.environ.get("BATRU_API_BASE", "https://batru.gg"))


@pytest.mark.live
def test_live_winrate(live_client):
    monkey = server._client
    server._client = live_client
    try:
        out = server.predict_dota_winrate(["antimage", "axe"], ["lina"])
        assert "error" not in out, out
        assert 0 <= out["my_win_rate_pct"] <= 100
        assert abs(out["my_win_rate_pct"] + out["enemy_win_rate_pct"] - 100) < 0.5
    finally:
        server._client = monkey


@pytest.mark.live
def test_live_counters(live_client):
    monkey = server._client
    server._client = live_client
    try:
        out = server.get_dota_counters("antimage")
        assert "error" not in out, out
        assert out["hero"] == "Anti-Mage"
        assert out["best_against"], "expected at least one favourable matchup"
        assert "winrate_pct" in out["best_against"][0]
    finally:
        server._client = monkey


# --- internal-run marker (data hygiene) -------------------------------------

def test_user_agent_internal_env_override(monkeypatch):
    from batru_mcp.client import _user_agent

    monkeypatch.setenv("BATRU_INTERNAL", "1")
    assert _user_agent().endswith(" (internal)")
    monkeypatch.setenv("BATRU_INTERNAL", "0")
    assert "(internal)" not in _user_agent()


def test_user_agent_auto_internal_from_git_checkout(monkeypatch):
    # This test suite runs from the dev checkout (a .git dir exists), so with
    # no env override the UA must self-mark as internal — registry installs
    # ship no .git and therefore never get the marker.
    from batru_mcp.client import _user_agent

    monkeypatch.delenv("BATRU_INTERNAL", raising=False)
    assert _user_agent().endswith(" (internal)")
