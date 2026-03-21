# FPL CLI Tool

A terminal-based Fantasy Premier League tool for team **237758**.

## Features

| Command | Description |
|---|---|
| `squad` | Show current GW picks with next 5 fixtures & colour-coded FDR |
| `transfers` | Suggest up to 2 upgrades within your current budget |
| `captain` | Rank your top 3 captaincy options for the current GW |

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
# View squad with fixture difficulty ratings
python fpl_tool.py squad

# Get transfer suggestions
python fpl_tool.py transfers

# Get captain recommendations
python fpl_tool.py captain

# Help
python fpl_tool.py --help
```

## Output

**FDR colour coding:**

| FDR | Colour | Meaning |
|---|---|---|
| 1 | Bold Green | Very easy |
| 2 | Green | Easy |
| 3 | Yellow | Medium |
| 4 | Red | Hard |
| 5 | Bold Red | Very hard |

**Transfer & captain scoring formula:**
`score = form × 0.5 + (ICT / 10) × 0.3 + fixture_ease × 0.2`

Uses the public [FPL API](https://fantasy.premierleague.com/api/) — no credentials required.
