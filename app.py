"""FPL Insights — Streamlit frontend for squad, transfers, and captain analysis."""

import streamlit as st
import httpx
import pandas as pd
from collections import defaultdict

st.set_page_config(page_title="FPL Insights", page_icon="⚽", layout="centered")

BASE_URL = "https://fantasy.premierleague.com/api"
DEFAULT_TEAM_ID = 237758
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://fantasy.premierleague.com/",
    "Origin": "https://fantasy.premierleague.com",
    "Connection": "keep-alive",
}

POSITION_NAMES = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
FDR_EMOJI = {1: "🟢", 2: "🟢", 3: "🟡", 4: "🟠", 5: "🔴"}


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
        st.error(f"FPL API returned {e.response.status_code} for {path}")
        st.stop()
    except httpx.ProxyError:
        st.error("A proxy is blocking the FPL API. Try again from a different network.")
        st.stop()
    except httpx.RequestError as e:
        st.error(f"Network error reaching FPL API: {e}")
        st.stop()


@st.cache_data(ttl=300, show_spinner=False)
def load_all(team_id: int) -> tuple:
    bootstrap = fetch("/bootstrap-static/")
    players = {p["id"]: p for p in bootstrap["elements"]}
    teams = {t["id"]: t for t in bootstrap["teams"]}

    current_gw = next((e["id"] for e in bootstrap["events"] if e["is_current"]), None)
    next_gw = next((e["id"] for e in bootstrap["events"] if e["is_next"]), None)
    gw = current_gw or next_gw or 1

    raw_fixtures = fetch("/fixtures/?future=1")
    team_fixes: dict[int, list] = defaultdict(list)
    for f in raw_fixtures:
        if f.get("event") is None:
            continue
        team_fixes[f["team_h"]].append({
            "event": f["event"],
            "opponent": teams[f["team_a"]]["short_name"],
            "is_home": True,
            "fdr": f["team_h_difficulty"],
        })
        team_fixes[f["team_a"]].append({
            "event": f["event"],
            "opponent": teams[f["team_h"]]["short_name"],
            "is_home": False,
            "fdr": f["team_a_difficulty"],
        })
    for tid in team_fixes:
        team_fixes[tid].sort(key=lambda x: x["event"])
        team_fixes[tid] = team_fixes[tid][:5]

    picks_data = fetch(f"/entry/{team_id}/event/{gw}/picks/")
    return players, teams, gw, dict(team_fixes), picks_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fix_str(fix: dict) -> str:
    emoji = FDR_EMOJI.get(fix["fdr"], "⚪")
    venue = "H" if fix["is_home"] else "A"
    return f"{emoji}{fix['opponent']}({venue})"


def avg_fdr(fixes: list) -> float:
    return sum(f["fdr"] for f in fixes) / len(fixes) if fixes else 3.0


def player_score(p: dict, fixes: list) -> float:
    form = float(p.get("form") or 0)
    ict = float(p.get("ict_index") or 0)
    ease = 6 - avg_fdr(fixes[:3])
    return form * 0.5 + (ict / 10) * 0.3 + ease * 0.2


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("⚽ FPL Insights")

with st.sidebar:
    team_id = st.number_input("Team ID", value=DEFAULT_TEAM_ID, step=1)
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

with st.spinner("Loading FPL data…"):
    players, teams, gw, team_fixes, picks_data = load_all(int(team_id))

picks = picks_data["picks"]
history = picks_data.get("entry_history", {})
bank = history.get("bank", 0) / 10
chip = picks_data.get("active_chip")

col1, col2, col3 = st.columns(3)
col1.metric("Gameweek", gw)
col2.metric("Bank", f"£{bank:.1f}m")
col3.metric("Chip", chip.upper() if chip else "—")

st.divider()

tab_squad, tab_transfers, tab_captain = st.tabs(["📋 Squad", "🔄 Transfers", "⭐ Captain"])

# ---------------------------------------------------------------------------
# Squad tab
# ---------------------------------------------------------------------------
with tab_squad:
    starters, bench = [], []
    for pick in picks:
        p = players.get(pick["element"])
        if not p:
            continue
        fixes = team_fixes.get(p["team"], [])
        name = p["web_name"]
        if pick["is_captain"]:
            name += " ©"
        elif pick["is_vice_captain"]:
            name += " vc"
        row = {
            "Pos": POSITION_NAMES.get(p["element_type"], "?"),
            "Player": name,
            "£": f"{p['now_cost'] / 10:.1f}",
            "Club": teams[p["team"]]["short_name"],
            **{f"GW+{i + 1}": fix_str(fixes[i]) if i < len(fixes) else "—" for i in range(5)},
        }
        if pick["position"] <= 11:
            starters.append(row)
        else:
            bench.append(row)

    st.subheader("Starting XI")
    st.dataframe(pd.DataFrame(starters), use_container_width=True, hide_index=True)

    st.subheader("Bench")
    st.dataframe(pd.DataFrame(bench), use_container_width=True, hide_index=True)

    st.caption("🟢 easy · 🟡 medium · 🟠 hard · 🔴 very hard")

# ---------------------------------------------------------------------------
# Transfers tab
# ---------------------------------------------------------------------------
with tab_transfers:
    squad_ids = {pick["element"] for pick in picks}
    bank_raw = history.get("bank", 0)

    squad_scored = []
    for pick in picks:
        if pick["position"] > 11:
            continue
        p = players[pick["element"]]
        fixes = team_fixes.get(p["team"], [])
        squad_scored.append((player_score(p, fixes), p))
    squad_scored.sort(key=lambda x: x[0])

    suggestions = []
    for _, sell in squad_scored[:4]:
        sell_cost = sell["now_cost"]
        budget = sell_cost + bank_raw
        pos_type = sell["element_type"]

        candidates = [
            p for p in players.values()
            if p["element_type"] == pos_type
            and p["id"] not in squad_ids
            and p["now_cost"] <= budget
            and p.get("status") == "a"
        ]
        if not candidates:
            continue

        sell_fixes = team_fixes.get(sell["team"], [])
        candidates.sort(
            key=lambda p: player_score(p, team_fixes.get(p["team"], [])),
            reverse=True,
        )
        buy = candidates[0]
        buy_fixes = team_fixes.get(buy["team"], [])

        if player_score(buy, buy_fixes) <= player_score(sell, sell_fixes):
            continue

        delta = (buy["now_cost"] - sell_cost) / 10
        delta_str = f"-£{abs(delta):.1f}m" if delta > 0 else f"+£{abs(delta):.1f}m"
        suggestions.append({
            "sell": sell,
            "buy": buy,
            "buy_fixes": buy_fixes,
            "sell_fixes": sell_fixes,
            "delta": delta,
            "delta_str": delta_str,
        })
        if len(suggestions) == 2:
            break

    if not suggestions:
        st.success("No obvious upgrades found — squad looks solid! 💪")
    else:
        st.caption(
            f"Bank: £{bank:.1f}m · GW{gw} · "
            "Score = form×0.5 + ICT/10×0.3 + fixture ease×0.2"
        )
        for s in suggestions:
            sell, buy = s["sell"], s["buy"]
            with st.container(border=True):
                c1, c2 = st.columns(2)
                c1.markdown(f"🔴 **OUT: {sell['web_name']}**")
                c1.caption(
                    f"£{sell['now_cost'] / 10:.1f}m · "
                    f"Form: {sell.get('form', '0')} · "
                    f"Next: {fix_str(s['sell_fixes'][0]) if s['sell_fixes'] else '—'}"
                )
                c2.markdown(f"🟢 **IN: {buy['web_name']}**")
                c2.caption(
                    f"£{buy['now_cost'] / 10:.1f}m · "
                    f"Form: {buy.get('form', '0')} · "
                    f"Next: {fix_str(s['buy_fixes'][0]) if s['buy_fixes'] else '—'}"
                )
                colour = "green" if s["delta"] <= 0 else "red"
                st.markdown(f"Cost change: :{colour}[**{s['delta_str']}**]")

# ---------------------------------------------------------------------------
# Captain tab
# ---------------------------------------------------------------------------
with tab_captain:
    starters_list = [pick for pick in picks if pick["position"] <= 11]
    scored = []
    for pick in starters_list:
        p = players[pick["element"]]
        fixes = team_fixes.get(p["team"], [])
        form = float(p.get("form") or 0)
        ict = float(p.get("ict_index") or 0)
        next_fix = fixes[0] if fixes else None
        ease = 6 - (next_fix["fdr"] if next_fix else 3)
        score = form * 0.5 + (ict / 10) * 0.3 + ease * 0.2
        scored.append({"score": score, "player": p, "next_fix": next_fix, "form": form, "ict": ict})

    scored.sort(key=lambda x: x["score"], reverse=True)

    medals = ["🥇", "🥈", "🥉"]
    for i, s in enumerate(scored[:3]):
        p = s["player"]
        with st.container(border=True):
            col_medal, col_info = st.columns([1, 5])
            col_medal.markdown(f"## {medals[i]}")
            col_info.markdown(
                f"**{p['web_name']}** · "
                f"{teams[p['team']]['short_name']} · "
                f"{POSITION_NAMES.get(p['element_type'], '?')}"
            )
            col_info.caption(
                f"Form: {s['form']} · ICT: {s['ict']:.1f} · "
                f"Next: {fix_str(s['next_fix']) if s['next_fix'] else '—'} · "
                f"Score: {s['score']:.2f}"
            )

    st.caption("Score = form×0.5 + ICT/10×0.3 + fixture ease×0.2")
