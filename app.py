"""FPL Insights — Streamlit frontend for squad, transfers, captain, and chip analysis."""

import json
import streamlit as st
import httpx
import pandas as pd
from collections import defaultdict
from groq import Groq

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


@st.cache_data(ttl=300, show_spinner=False)
def load_chip_data(team_id: int) -> tuple:
    """Fetch all future fixtures (no GW cap) and detect double gameweeks."""
    bootstrap = fetch("/bootstrap-static/")
    teams_local = {t["id"]: t for t in bootstrap["teams"]}

    raw_fixtures = fetch("/fixtures/?future=1")
    team_all: dict[int, list] = defaultdict(list)
    gw_team_counts: dict[int, dict[int, int]] = {}

    for f in raw_fixtures:
        ev = f.get("event")
        if ev is None:
            continue
        team_all[f["team_h"]].append({
            "event": ev,
            "opponent": teams_local[f["team_a"]]["short_name"],
            "is_home": True,
            "fdr": f["team_h_difficulty"],
        })
        team_all[f["team_a"]].append({
            "event": ev,
            "opponent": teams_local[f["team_h"]]["short_name"],
            "is_home": False,
            "fdr": f["team_a_difficulty"],
        })
        if ev not in gw_team_counts:
            gw_team_counts[ev] = {}
        gw_team_counts[ev][f["team_h"]] = gw_team_counts[ev].get(f["team_h"], 0) + 1
        gw_team_counts[ev][f["team_a"]] = gw_team_counts[ev].get(f["team_a"], 0) + 1

    for tid in team_all:
        team_all[tid].sort(key=lambda x: x["event"])

    double_gws: dict[int, list] = {}
    for ev, team_counts in gw_team_counts.items():
        dteams = [
            teams_local[tid]["short_name"]
            for tid, cnt in team_counts.items()
            if cnt >= 2
        ]
        if dteams:
            double_gws[ev] = sorted(dteams)

    remaining_gws = sorted(gw_team_counts.keys())
    return dict(team_all), double_gws, remaining_gws


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


def build_chip_context(
    picks: list,
    players: dict,
    teams: dict,
    gw: int,
    all_fixes: dict,
    double_gws: dict,
    remaining_gws: list,
    bank: float,
) -> dict:
    """Build a structured dict for the Groq LLM chip analysis."""
    squad_data = []
    for pick in picks:
        p = players.get(pick["element"])
        if not p:
            continue
        team_id = p["team"]
        player_fixes = all_fixes.get(team_id, [])

        # Build per-GW fixture map (supports double GWs with list of fixtures)
        gw_fixtures: dict[str, list] = {}
        for fix in player_fixes:
            ev = str(fix["event"])
            if ev not in gw_fixtures:
                gw_fixtures[ev] = []
            gw_fixtures[ev].append({
                "opponent": fix["opponent"],
                "home": fix["is_home"],
                "fdr": fix["fdr"],
            })

        squad_data.append({
            "name": p["web_name"],
            "position": POSITION_NAMES.get(p["element_type"], "?"),
            "team": teams[team_id]["short_name"],
            "is_starter": pick["position"] <= 11,
            "form": float(p.get("form") or 0),
            "ict": round(float(p.get("ict_index") or 0), 1),
            "cost": round(p["now_cost"] / 10, 1),
            "fixtures_by_gw": gw_fixtures,
        })

    return {
        "current_gameweek": gw,
        "remaining_gameweeks": remaining_gws,
        "bank_millions": bank,
        "chips_available": ["wildcard", "free_hit", "triple_captain", "bench_boost"],
        "double_gameweeks": {str(k): v for k, v in double_gws.items()},
        "squad": squad_data,
    }


def get_chip_recommendations(context: dict) -> dict:
    """Call Groq LLM and return parsed chip recommendations."""
    api_key = st.secrets.get("GROQ_API_KEY", "")
    if not api_key:
        st.error("GROQ_API_KEY not found in Streamlit secrets.")
        return {}

    client = Groq(api_key=api_key)

    system_prompt = """You are an elite Fantasy Premier League (FPL) analyst specialising in chip strategy.

Analyse the squad and fixture data and recommend the optimal gameweek to use each chip.

Return ONLY valid JSON with this exact structure (no markdown, no explanation):
{
  "triple_captain": {
    "gameweek": <int>,
    "player": "<name>",
    "fixture": "<opponent (H/A) FDR:<n>>",
    "reasoning": "<2-3 sentences explaining why>"
  },
  "bench_boost": {
    "gameweek": <int>,
    "key_bench_players": ["<name>", ...],
    "reasoning": "<2-3 sentences explaining why>"
  },
  "wildcard": {
    "gameweek": <int>,
    "strategy": "<brief squad rebuild advice>",
    "reasoning": "<2-3 sentences explaining why>"
  },
  "free_hit": {
    "gameweek": <int>,
    "teams_to_target": ["<short_name>", ...],
    "reasoning": "<2-3 sentences explaining why>"
  }
}

Strategy guidelines:
- Triple Captain: pick the GW where your best attacker/midfielder faces FDR 1-2 (home preferred)
- Bench Boost: pick a GW where ALL 15 players have good fixtures; double gameweeks are ideal
- Wildcard: recommend the GW just before a fixture swing where current squad suffers a bad run
- Free Hit: best in double gameweeks or when many current squad players have blanks
- Prioritise double gameweeks for Bench Boost and Free Hit
- Never recommend a gameweek already passed (current_gameweek)"""

    user_prompt = (
        f"Here is my FPL squad and fixture data:\n\n"
        f"{json.dumps(context, indent=2)}\n\n"
        "Recommend the optimal gameweek for each chip based on fixture difficulty, "
        "form, and double gameweek opportunities."
    )

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=1200,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else parts[0]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except json.JSONDecodeError as e:
        st.error(f"Could not parse LLM response as JSON: {e}")
        return {}
    except Exception as e:
        st.error(f"Groq API error: {e}")
        return {}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("⚽ FPL Insights")

with st.sidebar:
    team_id = st.number_input("Team ID", value=DEFAULT_TEAM_ID, step=1)
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.session_state.pop("chip_recs", None)
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

tab_squad, tab_transfers, tab_captain, tab_chips = st.tabs(
    ["📋 Squad", "🔄 Transfers", "⭐ Captain", "🃏 Chips"]
)

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

# ---------------------------------------------------------------------------
# Chips tab
# ---------------------------------------------------------------------------
with tab_chips:
    st.markdown("### 🃏 Chip Strategy Advisor")
    st.caption(
        "AI analysis of all remaining fixtures to recommend the optimal gameweek "
        "for each chip · Powered by Groq (llama-3.3-70b-versatile)"
    )

    if st.button("🤖 Analyse my chips", type="primary", use_container_width=True):
        st.session_state.pop("chip_recs", None)
        with st.spinner("Loading all fixtures & consulting AI…"):
            all_fixes, double_gws, remaining_gws = load_chip_data(int(team_id))

            # Show double GW info discovered
            if double_gws:
                dgw_lines = [
                    f"GW{gw_n}: {', '.join(teams_list)}"
                    for gw_n, teams_list in sorted(double_gws.items())
                ]
                st.info("**Double gameweeks detected:** " + " · ".join(dgw_lines))

            context = build_chip_context(
                picks, players, teams, gw,
                all_fixes, double_gws, remaining_gws, bank,
            )
            recs = get_chip_recommendations(context)
            if recs:
                st.session_state["chip_recs"] = recs

    if "chip_recs" in st.session_state:
        recs = st.session_state["chip_recs"]

        tc = recs.get("triple_captain", {})
        bb = recs.get("bench_boost", {})
        wc = recs.get("wildcard", {})
        fh = recs.get("free_hit", {})

        col_a, col_b = st.columns(2)

        with col_a:
            with st.container(border=True):
                st.markdown(f"#### 3️⃣ Triple Captain")
                st.markdown(f"**Gameweek:** GW{tc.get('gameweek', '?')}")
                st.markdown(f"**Player:** {tc.get('player', '?')}")
                if tc.get("fixture"):
                    st.markdown(f"**Fixture:** {tc['fixture']}")
                st.info(tc.get("reasoning", "—"))

        with col_b:
            with st.container(border=True):
                st.markdown(f"#### 🔋 Bench Boost")
                st.markdown(f"**Gameweek:** GW{bb.get('gameweek', '?')}")
                bench_players = bb.get("key_bench_players", [])
                if bench_players:
                    st.markdown(f"**Key bench players:** {', '.join(bench_players)}")
                st.info(bb.get("reasoning", "—"))

        col_c, col_d = st.columns(2)

        with col_c:
            with st.container(border=True):
                st.markdown(f"#### 🃏 Wildcard")
                st.markdown(f"**Gameweek:** GW{wc.get('gameweek', '?')}")
                if wc.get("strategy"):
                    st.markdown(f"**Strategy:** {wc['strategy']}")
                st.info(wc.get("reasoning", "—"))

        with col_d:
            with st.container(border=True):
                st.markdown(f"#### 🎯 Free Hit")
                st.markdown(f"**Gameweek:** GW{fh.get('gameweek', '?')}")
                target_teams = fh.get("teams_to_target", [])
                if target_teams:
                    st.markdown(f"**Teams to target:** {', '.join(target_teams)}")
                st.info(fh.get("reasoning", "—"))
    else:
        st.markdown(
            "Click **Analyse my chips** to get AI-powered recommendations "
            "based on your squad's upcoming fixtures."
        )
