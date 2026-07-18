"""Thin HTTP client over batru.gg's public, read-only prediction API.

This never loads a model or re-implements scoring: every number comes from the
live, calibrated production model served at ``batru.gg``. One shared
``httpx.Client`` plus an in-process cache for the (rarely changing) hero rosters
and counter table.

Endpoint contracts verified live against https://batru.gg (2026-06):

  GET  /api/data/heroes            -> {"constants": {"heroes": [ {id, name,
                                       displayName, shortName, aliases[]}, ... ]}}
  POST /api/winrate                 {"radiant":[shortName...], "dire":[...]}
                                     -> {"radiant_win_rate": float%,
                                         "dire_win_rate": float%}   (empty -> 50/50)
  POST /api/recommend               {"radiant":[...], "dire":[...],
                                       "is_our_team_dire": bool}
                                     -> [ {hero_key, name, win_rate%, next_picks}, ... ] (top 3)
  GET  /api/content/hero-counters   -> {"patch", "heroes": [ {hero_id, name,
                                       short_name, vs:[ {opponent_id, opponent_name,
                                       opponent_slug, winrate(0-1), matches} ]} ]}
  GET  /api/deadlock/heroes         -> [ {id:int, name:str}, ... ]
  POST /api/deadlock/predict/draft  {"team0_heroes":[6 int], "team1_heroes":[6 int]}
                                     -> {"win_prob_team0": float (0-1)}
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from . import __version__
from .hero_lookup import HeroResolver


def _user_agent() -> str:
    """``batru-mcp/<version>``, plus an `` (internal)`` marker when this run is
    our own rather than a real user's. The server (batru.gg
    ``core/client_analytics.py``) keeps internal traffic out of product
    analytics — the machine-channel mirror of the browser ``?batru_internal``
    gate. Detection: explicit ``BATRU_INTERNAL=1/0`` wins; otherwise internal
    iff running from a git checkout (registry installs ship no ``.git``, so
    real users are never marked)."""
    override = os.environ.get("BATRU_INTERNAL")
    if override is not None:
        internal = override.strip().lower() in ("1", "true", "yes")
    else:
        internal = (Path(__file__).resolve().parent.parent / ".git").exists()
    ua = f"batru-mcp/{__version__}"
    return f"{ua} (internal)" if internal else ua


class BatruApiError(Exception):
    """Raised when the API is unreachable or returns a bad response."""


class BatruClient:
    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"User-Agent": _user_agent()},
        )
        self._dota_resolver: Optional[HeroResolver] = None
        self._deadlock_resolver: Optional[HeroResolver] = None
        self._counters: Optional[Dict[str, dict]] = None

    # --- low-level ---------------------------------------------------------

    def _get(self, path: str) -> object:
        try:
            resp = self._http.get(path)
        except httpx.RequestError as e:
            raise BatruApiError(f"Could not reach batru.gg ({path}): {e}") from e
        return self._unwrap(resp, path)

    def _post(self, path: str, payload: dict) -> object:
        try:
            resp = self._http.post(path, json=payload)
        except httpx.RequestError as e:
            raise BatruApiError(f"Could not reach batru.gg ({path}): {e}") from e
        return self._unwrap(resp, path)

    @staticmethod
    def _unwrap(resp: httpx.Response, path: str) -> object:
        if resp.status_code == 503:
            raise BatruApiError(
                "The batru.gg model is updating (HTTP 503) — try again shortly."
            )
        if resp.status_code != 200:
            raise BatruApiError(f"batru.gg returned HTTP {resp.status_code} for {path}.")
        try:
            return resp.json()
        except ValueError as e:
            raise BatruApiError(f"batru.gg returned non-JSON for {path}.") from e

    # --- hero rosters (cached) --------------------------------------------

    def dota_resolver(self) -> HeroResolver:
        if self._dota_resolver is None:
            data = self._get("/api/data/heroes")
            try:
                heroes = data["constants"]["heroes"]  # type: ignore[index]
            except (TypeError, KeyError) as e:
                raise BatruApiError("Unexpected /api/data/heroes shape.") from e
            self._dota_resolver = HeroResolver.from_dota(heroes)
        return self._dota_resolver

    def deadlock_resolver(self) -> HeroResolver:
        if self._deadlock_resolver is None:
            data = self._get("/api/deadlock/heroes")
            if not isinstance(data, list):
                raise BatruApiError("Unexpected /api/deadlock/heroes shape.")
            self._deadlock_resolver = HeroResolver.from_deadlock(data)
        return self._deadlock_resolver

    # --- predictions -------------------------------------------------------

    def winrate(self, radiant: List[str], dire: List[str]) -> dict:
        data = self._post("/api/winrate", {"radiant": radiant, "dire": dire})
        if not isinstance(data, dict) or "radiant_win_rate" not in data:
            raise BatruApiError("Unexpected /api/winrate response.")
        return data

    def recommend(self, radiant: List[str], dire: List[str], is_our_team_dire: bool) -> List[dict]:
        data = self._post(
            "/api/recommend",
            {"radiant": radiant, "dire": dire, "is_our_team_dire": is_our_team_dire},
        )
        if not isinstance(data, list):
            raise BatruApiError("Unexpected /api/recommend response.")
        return data

    def counters(self) -> Dict[str, dict]:
        """Counter table keyed by hero short_name (cached)."""
        if self._counters is None:
            data = self._get("/api/content/hero-counters")
            try:
                heroes = data["heroes"]  # type: ignore[index]
            except (TypeError, KeyError) as e:
                raise BatruApiError("Unexpected /api/content/hero-counters shape.") from e
            self._counters = {h["short_name"]: h for h in heroes}
        return self._counters

    def deadlock_predict(self, team0_ids: List[int], team1_ids: List[int]) -> dict:
        data = self._post(
            "/api/deadlock/predict/draft",
            {"team0_heroes": team0_ids, "team1_heroes": team1_ids},
        )
        if not isinstance(data, dict) or "win_prob_team0" not in data:
            raise BatruApiError("Unexpected /api/deadlock/predict/draft response.")
        return data
