#!/usr/bin/env python3
"""FPL CLI Tool — squad overview, transfer suggestions, captain picks."""

import sys
from collections import defaultdict

import click
import httpx
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

BASE_URL = "https://fantasy.premierleague.com/api"
TEAM_ID = 237758
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://fantasy.premierleague.com/",
    "Origin": "https://fantasy.premierleague.com",
    "Connection": "keep-alive",
}

console = Console()


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def fetch(path: str) -> dict:
    url = f"{BASE_URL}{path}"
    try:
        r = httpx.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        console.print(f"[red]HTTP {e.response.status_code} error fetching {url}[/red]")
        sys.exit(1)
    except httpx.ProxyError:
        console.print(
            "[red]Proxy blocked the FPL API.[/red] "
            "[yellow]Run this tool without a restricting proxy or from a local machine.[/yellow]"
        )
        sys.exit(1)
    except httpx.RequestError as e:
        console.print(f"[red]Network error reaching FPL API: {e}[/red]")
        sys.exit(1)


def load_bootstrap():
    data = fetch("/bootstrap-static/")
    players = {p["id"]: p for p in data["elements"]}
    teams = {t["id"]: t for t in data["teams"]}
    current_gw = next((e["id"] for e in data["events"] if e["is_current"]), None)
    next_gw = next((e["id"] for e in data["events"] if e["is_next"]), None)
    active_gw = current_gw or next_gw or 1
    return players, teams, active_gw


def load_fixtures(teams: dict) -> dict[int, list[dict]]:
    """Return a map of team_id -> list of next fixtures (sorted by event)."""
    all_fixtures = fetch("/fixtures/?future=1")
    team_fixtures: dict[int, list[dict]] = defaultdict(list)
    for f in all_fixtures:
        if f.get("event") is None:
            continue
        team_fixtures[f["team_h"]].append({
            "event": f["event"],
            "opponent_id": f["team_a"],
            "opponent": teams[f["team_a"]]["short_name"],
            "is_home": True,
            "fdr": f["team_h_difficulty"],
        })
        team_fixtures[f["team_a"]].append({
            "event": f["event"],
            "opponent_id": f["team_h"],
            "opponent": teams[f["team_h"]]["short_name"],
            "is_home": False,
            "fdr": f["team_a_difficulty"],
        })
    # Sort and keep next 5 per team
    for tid in team_fixtures:
        team_fixtures[tid].sort(key=lambda x: x["event"])
        team_fixtures[tid] = team_fixtures[tid][:5]
    return team_fixtures


def load_picks(gw: int) -> dict:
    return fetch(f"/entry/{TEAM_ID}/event/{gw}/picks/")


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

POSITION_NAMES = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def fdr_color(fdr: int) -> str:
    return {1: "bold green", 2: "green", 3: "yellow", 4: "red", 5: "bold red"}.get(fdr, "white")


def fixture_cell(fix: dict) -> Text:
    label = f"{fix['opponent']}({'H' if fix['is_home'] else 'A'}) {fix['fdr']}"
    return Text(label, style=fdr_color(fix["fdr"]))


def avg_fdr(fixtures: list[dict]) -> float:
    if not fixtures:
        return 3.0
    return sum(f["fdr"] for f in fixtures) / len(fixtures)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """FPL CLI — squad, transfers, captain commands for team 237758."""


@cli.command()
def squad():
    """Show current picks with next 5 fixtures and FDR."""
    console.print("[bold cyan]Loading FPL data…[/bold cyan]")
    players, teams, gw = load_bootstrap()
    picks_data = load_picks(gw)
    fixtures = load_fixtures(teams)

    picks = picks_data["picks"]
    history = picks_data.get("entry_history", {})
    bank = history.get("bank", 0) / 10
    chip = picks_data.get("active_chip")

    title = f"GW{gw} Squad  |  Bank: £{bank:.1f}m"
    if chip:
        title += f"  |  Chip: {chip.upper()}"

    def make_table(label: str) -> Table:
        t = Table(
            title=label,
            box=box.SIMPLE_HEAVY,
            header_style="bold magenta",
            show_lines=False,
        )
        t.add_column("Pos", style="dim", width=4)
        t.add_column("Player", min_width=18)
        t.add_column("Cost", justify="right", width=6)
        t.add_column("Team", width=5)
        for i in range(1, 6):
            t.add_column(f"GW+{i}", min_width=12)
        return t

    starters_table = make_table(f"[bold]{title}[/bold]")
    bench_table = make_table("[bold]Bench[/bold]")

    for pick in picks:
        p = players.get(pick["element"])
        if not p:
            continue

        pos = POSITION_NAMES.get(p["element_type"], "?")
        name = p["web_name"]
        if pick["is_captain"]:
            name += " (C)"
        elif pick["is_vice_captain"]:
            name += " (VC)"

        cost = f"£{p['now_cost'] / 10:.1f}m"
        team_name = teams[p["team"]]["short_name"]
        team_fixes = fixtures.get(p["team"], [])

        fix_cells = [fixture_cell(team_fixes[i]) if i < len(team_fixes) else Text("-") for i in range(5)]
        row = [pos, name, cost, team_name] + fix_cells

        if pick["position"] <= 11:
            starters_table.add_row(*row)
        else:
            bench_table.add_row(*row)

    console.print()
    console.print(starters_table)
    console.print(bench_table)


@cli.command()
def transfers():
    """Suggest best 1-2 transfers within budget."""
    console.print("[bold cyan]Analysing transfer options…[/bold cyan]")
    players, teams, gw = load_bootstrap()
    picks_data = load_picks(gw)
    fixtures = load_fixtures(teams)

    picks = picks_data["picks"]
    history = picks_data.get("entry_history", {})
    bank_raw = history.get("bank", 0)

    squad_ids = {pick["element"] for pick in picks}

    def player_score(p: dict) -> float:
        """Higher is better: blend form, ICT, and upcoming fixture ease."""
        form = float(p.get("form") or 0)
        ict = float(p.get("ict_index") or 0)
        team_fixes = fixtures.get(p["team"], [])
        ease = 6 - avg_fdr(team_fixes[:3])  # invert FDR: easier = higher
        return form * 0.5 + (ict / 10) * 0.3 + ease * 0.2

    # Score every squad player — lowest scores are sell candidates
    squad_scored = []
    for pick in picks:
        if pick["position"] > 11:
            continue  # skip bench
        p = players[pick["element"]]
        squad_scored.append((player_score(p), p))
    squad_scored.sort(key=lambda x: x[0])

    table = Table(
        title="[bold]Suggested Transfers[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold magenta",
    )
    table.add_column("OUT", min_width=18)
    table.add_column("Cost", justify="right", width=6)
    table.add_column("Form", justify="right", width=6)
    table.add_column("→ IN", min_width=18)
    table.add_column("Cost", justify="right", width=6)
    table.add_column("Form", justify="right", width=6)
    table.add_column("Δ Cost", justify="right", width=8)
    table.add_column("Next Fix", min_width=12)

    suggestions_shown = 0
    for _, sell_player in squad_scored[:4]:  # consider bottom 4 starters
        sell_cost = sell_player["now_cost"]
        budget = sell_cost + bank_raw
        pos_type = sell_player["element_type"]

        # Find best available replacement
        candidates = [
            p for p in players.values()
            if p["element_type"] == pos_type
            and p["id"] not in squad_ids
            and p["now_cost"] <= budget
            and p.get("status") == "a"
        ]
        if not candidates:
            continue

        candidates.sort(key=player_score, reverse=True)
        buy = candidates[0]

        if player_score(buy) <= player_score(sell_player):
            continue  # no upgrade found

        delta = (buy["now_cost"] - sell_cost) / 10
        delta_str = f"[red]-£{abs(delta):.1f}m[/red]" if delta > 0 else f"[green]+£{abs(delta):.1f}m[/green]"

        buy_fixes = fixtures.get(buy["team"], [])
        next_fix = fixture_cell(buy_fixes[0]) if buy_fixes else Text("-")

        table.add_row(
            f"[red]{sell_player['web_name']}[/red]",
            f"£{sell_cost / 10:.1f}m",
            sell_player.get("form", "0"),
            f"[green]{buy['web_name']}[/green]",
            f"£{buy['now_cost'] / 10:.1f}m",
            buy.get("form", "0"),
            delta_str,
            next_fix,
        )
        suggestions_shown += 1
        if suggestions_shown == 2:
            break

    if suggestions_shown == 0:
        console.print(Panel("[green]No obvious upgrades found within budget — squad looks solid![/green]"))
    else:
        bank_display = bank_raw / 10
        console.print()
        console.print(f"[dim]Available bank: £{bank_display:.1f}m  |  GW{gw}[/dim]")
        console.print(table)
        console.print("[dim]Score = form×0.5 + ICT/10×0.3 + fixture ease×0.2[/dim]")


@cli.command()
def captain():
    """Rank top 3 captain picks for this GW."""
    console.print("[bold cyan]Evaluating captaincy options…[/bold cyan]")
    players, teams, gw = load_bootstrap()
    picks_data = load_picks(gw)
    fixtures = load_fixtures(teams)

    starters = [p for p in picks_data["picks"] if p["position"] <= 11]

    scored = []
    for pick in starters:
        p = players[pick["element"]]
        form = float(p.get("form") or 0)
        ict = float(p.get("ict_index") or 0)
        team_fixes = fixtures.get(p["team"], [])
        next_fdr = team_fixes[0]["fdr"] if team_fixes else 3
        next_opp = fixture_cell(team_fixes[0]) if team_fixes else Text("-")
        ease = 6 - next_fdr
        score = form * 0.5 + (ict / 10) * 0.3 + ease * 0.2
        scored.append((score, p, next_opp, form, ict, next_fdr))

    scored.sort(key=lambda x: x[0], reverse=True)
    top3 = scored[:3]

    table = Table(
        title=f"[bold]Top 3 Captain Picks — GW{gw}[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold magenta",
    )
    table.add_column("Rank", width=5, justify="center")
    table.add_column("Player", min_width=18)
    table.add_column("Team", width=5)
    table.add_column("Pos", width=4)
    table.add_column("Form", justify="right", width=6)
    table.add_column("ICT", justify="right", width=7)
    table.add_column("Next Fixture", min_width=14)
    table.add_column("Score", justify="right", width=7)

    medals = ["[bold yellow]1st[/bold yellow]", "[bold white]2nd[/bold white]", "[bold #cd7f32]3rd[/bold #cd7f32]"]

    for i, (score, p, next_opp, form, ict, _fdr) in enumerate(top3):
        table.add_row(
            medals[i],
            p["web_name"],
            teams[p["team"]]["short_name"],
            POSITION_NAMES.get(p["element_type"], "?"),
            str(form),
            f"{ict:.1f}",
            next_opp,
            f"{score:.2f}",
        )

    console.print()
    console.print(table)
    console.print("[dim]Score = form×0.5 + ICT/10×0.3 + fixture ease×0.2[/dim]")


if __name__ == "__main__":
    cli()
