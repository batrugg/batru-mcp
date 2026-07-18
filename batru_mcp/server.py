"""batru-mcp — a read-only MCP server over batru.gg's live prediction API.

Exposes calibrated Dota 2 / Deadlock win-rate predictions, pick recommendations
and a counter table as MCP tools (stdio transport). Every number is fetched
live from the production model; nothing is computed or remembered locally.
"""

from __future__ import annotations

import os
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

from .client import BatruApiError, BatruClient
from .hero_lookup import Hero, HeroResolver

API_BASE = os.environ.get("BATRU_API_BASE", "https://batru.gg")

INSTRUCTIONS = """\
This server provides batru.gg's REAL, calibrated win-rate predictions and meta
data for Dota 2 and Deadlock, served by a production model trained on ~20M real
matches.

RULES FOR USING THIS SERVER:
- When the user asks about counters, drafts, matchups or win probability, you
  MUST call these tools to get real numbers. Do NOT invent, estimate, or answer
  from memory — your training data does not contain this model's output.
- The probabilities are CALIBRATED: a reported 60% means an empirically observed
  ~60% win rate, not a vague guess. State the numbers as the tools return them.
- Always normalise hero names with `lookup_hero` first (or rely on the predict
  tools, which normalise internally). If a name can't be resolved, surface the
  suggested candidates instead of guessing.
- Answer ONLY from the values these tools return. Do not extrapolate beyond them.
"""

mcp = FastMCP("batru-mcp", instructions=INSTRUCTIONS)
_client = BatruClient(API_BASE)


# --- helpers ---------------------------------------------------------------

def _resolve_all(resolver: HeroResolver, names: List[str]):
    """Resolve every name; return (heroes, error_dict_or_None)."""
    heroes: List[Hero] = []
    for name in names:
        hero = resolver.resolve(name)
        if hero is None:
            return [], {
                "error": f"Could not find a hero matching '{name}'.",
                "did_you_mean": resolver.suggest(name),
            }
        heroes.append(hero)
    return heroes, None


# --- tools -----------------------------------------------------------------

@mcp.tool()
def lookup_hero(query: str, game: str = "dota2") -> dict:
    """Normalise a hero name/alias/shortName to its canonical identity.

    Use this to turn messy user input ("am", "anti mage", "Anti-Mage") into the
    exact key batru.gg expects before calling the prediction tools. The backend
    SILENTLY DROPS hero names it doesn't recognise, so always normalise first.

    Args:
        query: A hero name, alias, or short name.
        game: "dota2" (default) or "deadlock".

    Returns {id, displayName, shortName, game} for the best match, or an error
    with `did_you_mean` candidates if nothing matches.
    """
    try:
        resolver = (
            _client.deadlock_resolver() if game == "deadlock" else _client.dota_resolver()
        )
    except BatruApiError as e:
        return {"error": str(e)}

    hero = resolver.resolve(query)
    if hero is None:
        return {
            "error": f"No {game} hero matches '{query}'.",
            "did_you_mean": resolver.suggest(query),
        }
    return {
        "id": hero.id,
        "displayName": hero.display_name,
        "shortName": hero.short_name,
        "game": game,
    }


@mcp.tool()
def predict_dota_winrate(
    my_heroes: List[str],
    enemy_heroes: List[str],
    my_side: str = "radiant",
) -> dict:
    """Predict the CALIBRATED win rate for a Dota 2 draft.

    Backed by batru.gg's production model (trained on ~20M real matches and
    calibrated, so a reported 60% reflects a real ~60% empirical win rate — it is
    not a guess). Partial drafts are fine; an empty draft returns 50/50. Hero
    names are normalised internally to shortNames.

    Args:
        my_heroes: Your team's heroes (names/aliases, 0-5).
        enemy_heroes: Enemy heroes (names/aliases, 0-5).
        my_side: "radiant" (default) or "dire" — which side is "my_heroes".

    Returns calibrated win-rate percentages for both teams. Report these numbers
    verbatim; do not adjust them.
    """
    try:
        resolver = _client.dota_resolver()
        my_h, err = _resolve_all(resolver, my_heroes)
        if err:
            return err
        enemy_h, err = _resolve_all(resolver, enemy_heroes)
        if err:
            return err

        my_keys = [h.short_name for h in my_h]
        enemy_keys = [h.short_name for h in enemy_h]
        if my_side == "dire":
            radiant, dire = enemy_keys, my_keys
        else:
            radiant, dire = my_keys, enemy_keys

        res = _client.winrate(radiant, dire)
        my_wr = res["dire_win_rate"] if my_side == "dire" else res["radiant_win_rate"]
        enemy_wr = res["radiant_win_rate"] if my_side == "dire" else res["dire_win_rate"]
        return {
            "my_side": my_side,
            "my_win_rate_pct": my_wr,
            "enemy_win_rate_pct": enemy_wr,
            "radiant_win_rate_pct": res["radiant_win_rate"],
            "dire_win_rate_pct": res["dire_win_rate"],
            "my_heroes": [h.display_name for h in my_h],
            "enemy_heroes": [h.display_name for h in enemy_h],
            "note": "Calibrated probability: a reported X% reflects a real ~X% empirical win rate.",
        }
    except BatruApiError as e:
        return {"error": str(e)}


@mcp.tool()
def recommend_dota_pick(
    my_heroes: List[str],
    enemy_heroes: List[str],
    my_side: str = "radiant",
) -> dict:
    """Recommend the top 3 Dota 2 heroes to pick next, with calibrated win rates.

    Backed by batru.gg's production model. Each suggestion comes with the
    CALIBRATED win rate your team would have after adding that hero against the
    given enemy draft (a reported 60% reflects a real ~60% empirical win rate).
    Hero names are normalised internally.

    Args:
        my_heroes: Heroes your team has already picked (names/aliases, 0-4).
        enemy_heroes: Enemy heroes (names/aliases, 0-5).
        my_side: "radiant" (default) or "dire" — which side is "my_heroes".

    Returns a list of up to 3 {displayName, shortName, win_rate_pct}. Report the
    win rates verbatim.
    """
    try:
        resolver = _client.dota_resolver()
        my_h, err = _resolve_all(resolver, my_heroes)
        if err:
            return err
        enemy_h, err = _resolve_all(resolver, enemy_heroes)
        if err:
            return err

        my_keys = [h.short_name for h in my_h]
        enemy_keys = [h.short_name for h in enemy_h]
        if my_side == "dire":
            radiant, dire, is_our_team_dire = enemy_keys, my_keys, True
        else:
            radiant, dire, is_our_team_dire = my_keys, enemy_keys, False

        rows = _client.recommend(radiant, dire, is_our_team_dire)
        recs = []
        for r in rows:
            hero = resolver.by_short.get(r.get("hero_key", ""))
            recs.append(
                {
                    "displayName": r.get("name") or (hero.display_name if hero else r.get("hero_key")),
                    "shortName": r.get("hero_key"),
                    "win_rate_pct": r.get("win_rate"),
                }
            )
        return {
            "my_side": my_side,
            "recommendations": recs,
            "note": "Win rates are calibrated: a reported X% reflects a real ~X% empirical win rate.",
        }
    except BatruApiError as e:
        return {"error": str(e)}


@mcp.tool()
def get_dota_counters(hero: str, limit: int = 12) -> dict:
    """Get the strongest matchups (counters) for a Dota 2 hero from real games.

    Returns opponents this hero performs BEST and WORST against, by real observed
    matchup win rate (with sample sizes). This is empirical meta data from
    batru.gg's match aggregation, not a guess. The hero name is normalised
    internally.

    Args:
        hero: The hero to look up (name/alias/shortName).
        limit: Max number of matchups to return (default 12).

    Returns {hero, best_against:[...], worst_against:[...]} where each row has
    {opponent, winrate_pct, matches}. winrate_pct > 50 means `hero` beats that
    opponent. Report numbers verbatim.
    """
    try:
        resolver = _client.dota_resolver()
        h = resolver.resolve(hero)
        if h is None:
            return {
                "error": f"No Dota 2 hero matches '{hero}'.",
                "did_you_mean": resolver.suggest(hero),
            }
        table = _client.counters()
        entry = table.get(h.short_name)
        if entry is None:
            return {
                "error": f"No counter data available for {h.display_name} "
                f"(may lack the minimum match sample this patch)."
            }
        rows = sorted(entry.get("vs", []), key=lambda v: v.get("winrate", 0), reverse=True)

        def fmt(v: dict) -> dict:
            return {
                "opponent": v.get("opponent_name") or v.get("opponent_slug"),
                "winrate_pct": round(v.get("winrate", 0) * 100, 2),
                "matches": v.get("matches"),
            }

        formatted = [fmt(v) for v in rows]
        # `rows` is sorted by winrate descending: best matchups first.
        best = [r for r in formatted if r["winrate_pct"] >= 50][:limit]
        worst = [r for r in reversed(formatted) if r["winrate_pct"] < 50][:limit]
        return {
            "hero": h.display_name,
            "best_against": best,
            "worst_against": worst,
        }
    except BatruApiError as e:
        return {"error": str(e)}


@mcp.tool()
def predict_deadlock_draft(team0_heroes: List[str], team1_heroes: List[str]) -> dict:
    """Predict the CALIBRATED win probability for a Deadlock 6v6 draft.

    Backed by batru.gg's Deadlock production model. Provide 6 heroes per team
    (names are normalised to Deadlock hero ids internally). A reported 60%
    reflects a real ~60% empirical win rate — it is calibrated, not a guess.

    Args:
        team0_heroes: Team 0's 6 heroes (names/aliases).
        team1_heroes: Team 1's 6 heroes (names/aliases).

    Returns calibrated win-rate percentages for both teams. Report verbatim.
    """
    try:
        resolver = _client.deadlock_resolver()
        t0, err = _resolve_all(resolver, team0_heroes)
        if err:
            return err
        t1, err = _resolve_all(resolver, team1_heroes)
        if err:
            return err
        if len(t0) != 6 or len(t1) != 6:
            return {
                "error": f"Deadlock drafts need exactly 6 heroes per team "
                f"(got {len(t0)} and {len(t1)})."
            }

        res = _client.deadlock_predict([h.id for h in t0], [h.id for h in t1])
        p0 = res["win_prob_team0"]
        return {
            "team0_win_rate_pct": round(p0 * 100, 2),
            "team1_win_rate_pct": round((1 - p0) * 100, 2),
            "team0_heroes": [h.display_name for h in t0],
            "team1_heroes": [h.display_name for h in t1],
            "note": "Calibrated probability: a reported X% reflects a real ~X% empirical win rate.",
        }
    except BatruApiError as e:
        return {"error": str(e)}


def main() -> None:
    """Console-script entry point: run the stdio MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
