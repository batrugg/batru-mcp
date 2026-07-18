# batru-mcp

<!-- mcp-name: gg.batru/batru-mcp -->

A minimal, read-only **MCP server** over [batru.gg](https://batru.gg)'s live API, so your LLM can answer Dota 2 / Deadlock draft, counter and win-rate questions with **real, calibrated model predictions** instead of guessing from memory.

It is a thin wrapper around batru.gg's public endpoints — no model runs locally; every number comes from the same production model the website serves.

## Why calibrated matters

batru.gg's model is trained on ~20M real matches and **calibrated**: a reported 60% win rate corresponds to an empirically observed ~60% win rate. We deliberately do not headline a raw "accuracy" number — accuracy alone is misleading for win prediction. What you get from these tools are probabilities you can trust at face value. The tool descriptions instruct the host LLM to report these numbers verbatim and never invent matchup data.

## Tools

| Tool | What it does |
| --- | --- |
| `lookup_hero(query, game="dota2")` | Normalise a name/alias/shortName to `{id, displayName, shortName}`. `game` ∈ {`dota2`, `deadlock`}. |
| `predict_dota_winrate(my_heroes, enemy_heroes, my_side="radiant")` | Calibrated win-rate % for both teams (partial drafts OK; empty → 50/50). |
| `recommend_dota_pick(my_heroes, enemy_heroes, my_side="radiant")` | Top-3 heroes to pick next, each with its calibrated win rate. |
| `get_dota_counters(hero, limit=12)` | Real matchup table: who this hero beats / loses to, with win rate % and sample size. |
| `predict_deadlock_draft(team0_heroes, team1_heroes)` | Calibrated win-rate % for a Deadlock 6v6 (6 heroes per team). |

Hero names are accepted in any form (e.g. `am`, `anti mage`, `Anti-Mage`) and normalised internally — the backend silently drops names it doesn't recognise, so normalising first keeps predictions honest.

## Install

Requires [uv](https://docs.astral.sh/uv/) (or any way to run a Python 3.12+ package from PyPI):

```bash
uvx batru-mcp           # fetches from PyPI and starts the stdio MCP server
```

Configuration is via the `BATRU_API_BASE` environment variable (default `https://batru.gg`) — you normally don't need to set anything.

## Claude Desktop config

Add to `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "batru": {
      "command": "uvx",
      "args": ["batru-mcp"]
    }
  }
}
```

Restart Claude Desktop; the `batru` tools appear in the tool picker. Claude Code:
`claude mcp add batru -- uvx batru-mcp`.

## Development

```bash
git clone https://github.com/batrugg/batru-mcp && cd batru-mcp
uv sync
uv run batru-mcp        # run the server from the checkout (blocks, waiting on stdin)
```

For a Claude Desktop pointing at the checkout, use
`"command": "uv", "args": ["run", "--directory", "/absolute/path/to/batru-mcp", "batru-mcp"]`.

## Tests

```bash
uv run pytest            # offline: hero normalisation + draft assembly
uv run pytest -m live    # also hits the real batru.gg API
```

---

Prefer programmatic access from Python instead of MCP? `pip install batru` — the
official [batru SDK](https://pypi.org/project/batru/).
