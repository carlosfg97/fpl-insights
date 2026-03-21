# FPL Insights

A CLI tool for analysing Fantasy Premier League team 237758 — squad overview, transfer suggestions, and captain picks.

## Setup

```bash
pip install -r requirements.txt
```

## Running the tool

```bash
python fpl_tool.py squad       # Show current squad with fixtures
python fpl_tool.py transfers   # Suggest up to 2 optimal transfers
python fpl_tool.py captain     # Rank top 3 captaincy options
```

Add `--help` to any command for usage details.

## Architecture

Single-file application (`fpl_tool.py`) built with:

- **click** — CLI commands and option parsing
- **httpx** — HTTP requests to the public FPL API (`https://fantasy.premierleague.com/api/`)
- **rich** — Terminal tables, colour-coded output, and panels

### Key internals

| Component | Description |
|-----------|-------------|
| `fetch()` | Base HTTP helper with error handling for network/proxy issues |
| `load_bootstrap()` | Fetches all player and team data for the current season |
| `load_fixtures()` | Loads upcoming fixtures for FDR calculations |
| `load_picks()` | Fetches the current gameweek squad picks for `TEAM_ID` |
| `fdr_style()` | Maps FDR 1–5 to Rich colour strings for consistent display |

### Scoring formula (transfers & captain)

```
score = form × 0.5 + (ict_index / 10) × 0.3 + fixture_ease × 0.2
```

`fixture_ease` is the inverted average FDR across the next 5 fixtures (`6 - avg_fdr`).

## Configuration

`TEAM_ID` in `fpl_tool.py` is hardcoded to `237758`. Change it to target a different team.

## No authentication required

All data comes from the public FPL API; no login or API key is needed.
