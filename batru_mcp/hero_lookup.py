"""Game-agnostic hero name/alias resolution.

Turns messy user text ("am", "anti mage", "Anti-Mage") into the exact key the
batru.gg backend expects. The backend SILENTLY IGNORES hero names it doesn't
recognise (an unknown name just gets dropped from the draft and you still get a
plausible-looking win rate), so resolving locally and refusing unknown names is
the only way to keep predictions honest.

This module never talks to the network and never predicts anything — it only
normalises strings. The hero roster is injected, so it is fully testable
offline. Dota 2 keys on ``shortName`` (e.g. ``"antimage"``); Deadlock keys on
the integer ``id``.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


def _normalize(s: str) -> str:
    """Lowercase, drop everything that isn't a letter or digit.

    "Anti-Mage" / "anti_mage" / "Anti Mage" / "antimage" all collapse to the
    same key — forgiving lookups without fuzzy guessing.
    """
    return re.sub(r"[^a-z0-9]", "", s.lower())


@dataclass(frozen=True)
class Hero:
    id: int                 # Dota numeric id / Deadlock hero id
    short_name: str         # backend key for Dota ("phantom_assassin"); == display for Deadlock
    display_name: str       # human label ("Phantom Assassin")
    aliases: tuple = field(default=())  # extra search tokens (raw npc name, nicknames)


class HeroResolver:
    """Resolve free-form hero text to a single :class:`Hero`.

    Build it from a list of hero dicts (the shape each game's roster endpoint
    returns). For Dota pass the ``constants.heroes`` items; for Deadlock pass the
    ``[{"id", "name"}]`` items (``name`` is used as both short and display).
    """

    @classmethod
    def from_dota(cls, heroes: List[dict]) -> "HeroResolver":
        records = [
            Hero(
                id=h["id"],
                short_name=h["shortName"],
                display_name=h["displayName"],
                aliases=tuple([h.get("name", ""), *h.get("aliases", [])]),
            )
            for h in heroes
        ]
        return cls(records)

    @classmethod
    def from_deadlock(cls, heroes: List[dict]) -> "HeroResolver":
        records = [
            Hero(id=h["id"], short_name=h["name"], display_name=h["name"])
            for h in heroes
        ]
        return cls(records)

    def __init__(self, heroes: List[Hero]):
        self.heroes = heroes
        self.by_short: Dict[str, Hero] = {h.short_name: h for h in heroes}
        self.by_id: Dict[int, Hero] = {h.id: h for h in heroes}

        # normalized token -> Hero, built from short/display/aliases.
        self._index: Dict[str, Hero] = {}
        for hero in heroes:
            for token in (hero.short_name, hero.display_name, *hero.aliases):
                nk = _normalize(token)
                if nk:
                    self._index.setdefault(nk, hero)
        self._suggest_pool = list(self._index.keys())

    def resolve(self, token: str) -> Optional[Hero]:
        """Exact (normalized) match, else None."""
        return self._index.get(_normalize(token))

    def suggest(self, token: str, n: int = 3) -> List[str]:
        """Closest known display names for an unresolved token (typo help)."""
        norm = _normalize(token)
        if not norm:
            return []
        hits = difflib.get_close_matches(norm, self._suggest_pool, n=n * 2, cutoff=0.5)
        seen, out = set(), []
        for h in hits:
            hero = self._index[h]
            if hero.short_name not in seen:
                seen.add(hero.short_name)
                out.append(hero.display_name)
            if len(out) >= n:
                break
        return out
