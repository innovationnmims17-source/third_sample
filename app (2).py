"""
Build a Portfolio, Survive the News — Live Investing Simulation
=================================================================
A single-file Streamlit app for running the event live. No extra
packages needed beyond streamlit + pandas.

Two roles:
  - CONTROLLER — releases each news round, enters the market moves,
    awards quiz points, drives the Reveal, and can start a fresh game.
  - TEAM — logs in with their team name, rebalances every round
    (Nifty / Gold / Bitcoin / FD are set directly; Cash is always the
    auto-calculated leftover, so the total can never be wrong), and
    locks in each round.

Run with:
    streamlit run app.py --server.address 0.0.0.0

Game state lives in server memory (see get_store()), shared by every
browser connected to this one process — that's what makes rounds feel
simultaneous. Login (role + team name) is also mirrored into the URL's
query string, so a browser refresh (manual or automatic) never logs
anyone out or loses their place.
"""

import threading
from datetime import datetime

import streamlit as st
import pandas as pd

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
CONTROLLER_PIN = "1234"          # change this before the event!
ASSETS = ["Nifty 50", "Gold", "Bitcoin", "Fixed Deposit", "Cash"]
ASSET_KEYS = ["nifty", "gold", "bitcoin", "fd", "cash"]
ASSET_LABEL = dict(zip(ASSET_KEYS, ASSETS))
ADJUSTABLE_KEYS = ["nifty", "gold", "bitcoin", "fd"]   # Cash is auto-computed
AUTO_KEY = "cash"

MIN_AMT, MAX_AMT = 10_000, 50_000     # flat ₹ bounds, per the rules — NOT a %
STARTING_VALUE = 100_000
QUIZ_BONUS = 10_000
N_NEWS_ROUNDS = 8
REVEAL_IDX = N_NEWS_ROUNDS + 1     # round 9 internally, always shown as "The Reveal"
DEFAULT_TEAMS = ["abc", "The Hedgers", "The Safe Hands",
                  "The Contrarians", "The Analysts"]

ROUND_DEFS = [
    {"idx": 0, "name": "Allocation Round", "headline": None, "note": None},
    {"idx": 1, "name": "News Round 1",
     "headline": "RBI holds interest rates steady for the 7th consecutive "
                  "time amid sticky inflation.",
     "note": "FD stays flat · Nifty dips slightly · Gold holds strong",
     "default_pct": {"nifty": -1.0, "gold": 1.0, "bitcoin": 0.0,
                      "fd": 0.9, "cash": 0.0}},
    {"idx": 2, "name": "News Round 2",
     "headline": "Bitcoin crosses $100,000 for the first time. Analysts "
                  "divided on whether it sustains.",
     "note": "Bitcoin surges · Cash looks bad · Gold unaffected",
     "default_pct": {"nifty": 0.5, "gold": 0.0, "bitcoin": 15.0,
                      "fd": 0.9, "cash": 0.0}},
    {"idx": 3, "name": "News Round 3",
     "headline": "US recession fears grow. IT sector drags Nifty down 3% "
                  "in a single session.",
     "note": "Nifty drops · Gold spikes · FD looks smart now",
     "default_pct": {"nifty": -3.0, "gold": 4.0, "bitcoin": -2.0,
                      "fd": 0.9, "cash": 0.0}},
    {"idx": 4, "name": "News Round 4",
     "headline": "India Budget: capital gains tax hiked. Markets fall 2% "
                  "on announcement.",
     "note": "Nifty and Bitcoin hit · Cash and FD win · Gold rises on "
             "uncertainty",
     "default_pct": {"nifty": -2.0, "gold": 2.0, "bitcoin": -3.0,
                      "fd": 0.9, "cash": 0.0}},
    {"idx": 5, "name": "News Round 5",
     "headline": "Gold hits an all-time high as global uncertainty rises.",
     "note": "Gold jumps · Bitcoin dips slightly · Nifty flat",
     "default_pct": {"nifty": 0.5, "gold": 5.0, "bitcoin": -1.0,
                      "fd": 0.9, "cash": 0.0}},
    {"idx": 6, "name": "News Round 6",
     "headline": "Nifty rallies on strong Q2 earnings from IT majors.",
     "note": "Nifty jumps · Gold cools off · Bitcoin drifts up",
     "default_pct": {"nifty": 3.0, "gold": -1.0, "bitcoin": 1.0,
                      "fd": 0.9, "cash": 0.0}},
    {"idx": 7, "name": "News Round 7",
     "headline": "US Fed cuts interest rates by 25 bps, fuelling a "
                  "risk-on rally.",
     "note": "Bitcoin surges on cheap money · Nifty up · Gold up slightly",
     "default_pct": {"nifty": 1.5, "gold": 1.0, "bitcoin": 8.0,
                      "fd": 0.9, "cash": 0.0}},
    {"idx": 8, "name": "News Round 8",
     "headline": "Oil prices spike after a Middle East supply disruption.",
     "note": "Nifty dips on inflation fears · Gold up · Bitcoin wobbles",
     "default_pct": {"nifty": -1.5, "gold": 2.5, "bitcoin": -1.0,
                      "fd": 0.9, "cash": 0.0}},
    {"idx": REVEAL_IDX, "name": "The Reveal",
     "headline": "Live prices pulled up — final portfolio values calculated.",
     "note": None,
     "default_pct": {"nifty": 0.0, "gold": 0.0, "bitcoin": 0.0,
                      "fd": 0.0, "cash": 0.0}},
]

QUIZ_QUESTIONS = [
    ("What does \u201cNifty 50\u201d actually represent?",
     "Top 50 companies on the NSE by market cap"),
    ("What is the maximum number of Bitcoins that will ever exist?",
     "21 million"),
    ("In investing, what does \u201cdiversification\u201d mean?",
     "Spreading money across different assets to reduce risk"),
    ("What does FD stand for and what is its defining feature vs. equity?",
     "Fixed Deposit \u2014 guaranteed, fixed return"),
    ("If inflation rises and interest rates stay flat, what happens to "
     "real returns on cash?", "They fall (cash loses purchasing power)"),
    ("Why is gold called a \u201csafe haven\u201d asset?",
     "Investors flock to it during uncertainty/crises, so it holds value"),
    ("What body sets India\u2019s benchmark interest rates?",
     "The Reserve Bank of India (RBI)"),
    ("What is \u201cvolatility\u201d in market terms?",
     "How sharply/frequently an asset\u2019s price swings"),
    ("What does it mean if a stock market \u201ccorrects\u201d?",
     "It falls roughly 10% or more from a recent high"),
    ("Name one reason capital gains tax hikes can make markets fall.",
     "Reduces after-tax returns, so demand for equities drops"),
]

# ----------------------------------------------------------------------
# SHARED STATE (in-memory, shared across every connected browser)
# ----------------------------------------------------------------------
@st.cache_resource
def get_lock():
    return threading.Lock()


def build_store():
    """Fresh game-state dict (also used to reset mid-rehearsal)."""
    return {
        "teams": {},            # team_name -> team dict
        "current_round": 0,
        "rounds": {r["idx"]: {
            "released": r["idx"] == 0,   # allocation round is "open" from the start
            "results_applied": False,
            "headline": r["headline"],
            "note": r["note"],
            "price_pct": dict(r.get("default_pct", {})) if r.get("default_pct") else None,
        } for r in ROUND_DEFS},
        "revealed": False,
        "registered_teams": list(DEFAULT_TEAMS),
        "event_started_at": None,
        "last_rank_snapshot": {},   # team_name -> rank, as of the last applied round
        "rank_movement": {},        # team_name -> +up / -down since previous round
    }


@st.cache_resource
def get_store():
    """One dict, shared by every session on this server process."""
    return build_store()


def new_team(name):
    return {
        "name": name,
        "assets": None,          # dict asset_key -> ₹ amount, set after round-0 submit
        "value_history": [],     # list of (round_idx, round_name, value)
        "quiz_points": 0,
        "submitted_rounds": set(),
    }


def total_value(team):
    if team["assets"] is None:
        return STARTING_VALUE
    return sum(team["assets"].values())


def round_def(idx):
    return next(r for r in ROUND_DEFS if r["idx"] == idx)


def stage_label(cur):
    """Human label that never implies there are 9 rounds when there are 8."""
    if cur == 0:
        return "Allocation Round"
    if cur == REVEAL_IDX:
        return "The Reveal"
    return f"News Round {cur} of {N_NEWS_ROUNDS}"


def bounds_for(total):
    """Flat ₹10,000 / ₹50,000 bounds, with a graceful fallback only if a
    portfolio ever shrinks so far that ₹10k x 5 wouldn't fit."""
    min_amt = MIN_AMT if total >= 5 * MIN_AMT else max(0, int(total / 5))
    max_amt = min(MAX_AMT, int(total)) if total < MAX_AMT else MAX_AMT
    if max_amt < min_amt:
        max_amt = min_amt
    return int(min_amt), int(max_amt)


def reset_scores_keep_teams(store):
    """New Game: wipes progress/scores but keeps the registered team list
    so nobody has to re-type their team name."""
    fresh = build_store()
    fresh["registered_teams"] = list(store["registered_teams"])
    for k in list(store.keys()):
        del store[k]
    store.update(fresh)
    # Also clear any left-over per-user pending-allocation widget state
    for k in list(st.session_state.keys()):
        if k.startswith("_pend_"):
            del st.session_state[k]


# ----------------------------------------------------------------------
# STYLE
# ----------------------------------------------------------------------
def inject_css():
    st.markdown("""
    <style>
    .stApp { background: radial-gradient(circle at top left, #101826 0%, #0b0f17 55%, #05070b 100%); }
    section.main > div { padding-top: 1.2rem; }
    h1, h2, h3 { font-family: 'Segoe UI', sans-serif; }
    .headline-card {
        background: linear-gradient(135deg, #1b2740, #101827);
        border: 1px solid #2f4066;
        border-radius: 16px;
        padding: 22px 26px;
        margin-bottom: 14px;
        box-shadow: 0 8px 24px rgba(0,0,0,0.35);
    }
    .headline-card h2 { color: #ffcf5c; margin-top:0; }
    .headline-text { font-size: 1.25rem; color: #f4f6fb; font-weight: 600; }
    .waiting-box {
        text-align:center; padding: 60px 20px; border-radius:18px;
        background: linear-gradient(135deg,#151c2c,#0c111c);
        border: 1px dashed #3a4a6b; color:#9fb0d0; font-size:1.1rem;
    }
    .pulse { animation: pulse 1.6s ease-in-out infinite; }
    @keyframes pulse { 0%{opacity:.55} 50%{opacity:1} 100%{opacity:.55} }
    .score-row {
        display:flex; justify-content:space-between; align-items:center;
        background:#131b2b; border:1px solid #263453; border-radius:12px;
        padding:10px 18px; margin-bottom:8px; transition: all .3s ease;
    }
    .score-rank1 { border-color:#ffcf5c; box-shadow:0 0 14px rgba(255,207,92,.35); }
    .badge { display:inline-block; padding:2px 10px; border-radius:999px;
        background:#26314a; color:#9fb0d0; font-size:.75rem; margin-left:8px;}
    .bar-track { background:#0d1420; border-radius:6px; height:10px; margin-top:6px; overflow:hidden; }
    .bar-fill { background:linear-gradient(90deg,#4c6fff,#8a5cff); height:100%; border-radius:6px; }
    </style>
    """, unsafe_allow_html=True)


def auto_refresh(seconds=4):
    """Dependency-free auto-refresh: a real browser reload every N seconds.
    Only ever call this from a *read-only / waiting* screen — never while
    a form is being actively filled in, since a reload can't preserve
    unsubmitted widget input. Login (role/team) survives the reload via
    the URL query string, so nobody gets logged out."""
    st.markdown(f"<meta http-equiv='refresh' content='{seconds}'>",
                unsafe_allow_html=True)


# ----------------------------------------------------------------------
# SESSION PERSISTENCE (survives browser refresh via the URL)
# ----------------------------------------------------------------------
def restore_session():
    if "role" in st.session_state:
        return
    qp_role = st.query_params.get("role")
    qp_team = st.query_params.get("team")
    if qp_role == "controller":
        st.session_state.role = "controller"
    elif qp_role == "team" and qp_team:
        st.session_state.role = "team"
        st.session_state.team_name = qp_team
    else:
        st.session_state.role = None


def login_as_team(team_name):
    st.session_state.role = "team"
    st.session_state.team_name = team_name
    st.query_params["role"] = "team"
    st.query_params["team"] = team_name


def login_as_controller():
    st.session_state.role = "controller"
    st.query_params["role"] = "controller"
    if "team" in st.query_params:
        del st.query_params["team"]


def logout():
    st.session_state.role = None
    st.session_state.pop("team_name", None)
    st.query_params.clear()


# ----------------------------------------------------------------------
# LOGIN
# ----------------------------------------------------------------------
def page_login(store):
    st.markdown("<h1 style='text-align:center;'>\U0001F4C8 Build a Portfolio, Survive the News</h1>",
                unsafe_allow_html=True)
    st.markdown("<p style='text-align:center;color:#9fb0d0;'>Live Investing Simulation</p>",
                unsafe_allow_html=True)
    st.write("")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### \U0001F3AE Join as a Team")
        team = st.selectbox("Choose your team", store["registered_teams"])
        if st.button("Enter Team Room", use_container_width=True, type="primary"):
            with get_lock():
                if team not in store["teams"]:
                    store["teams"][team] = new_team(team)
            login_as_team(team)
            st.rerun()
    with c2:
        st.markdown("### \U0001F39B\uFE0F Controller Login")
        pin = st.text_input("Controller PIN", type="password")
        if st.button("Enter Control Room", use_container_width=True):
            if pin == CONTROLLER_PIN:
                if store["event_started_at"] is None:
                    store["event_started_at"] = datetime.now().strftime("%H:%M:%S")
                login_as_controller()
                st.rerun()
            else:
                st.error("Wrong PIN.")


# ----------------------------------------------------------------------
# SCOREBOARD (shared component)
# ----------------------------------------------------------------------
def compute_leaderboard(store, apply_quiz_bonus=False, sort_by="Final Value"):
    teams = store["teams"]
    if not teams:
        return pd.DataFrame(columns=["Team", "Portfolio Value", "Quiz Points", "Final Value"])
    max_quiz = max((t["quiz_points"] for t in teams.values()), default=0)
    rows = []
    for t in teams.values():
        val = total_value(t)
        bonus = QUIZ_BONUS if (apply_quiz_bonus and t["quiz_points"] == max_quiz and max_quiz > 0) else 0
        rows.append({
            "Team": t["name"],
            "Portfolio Value": round(val),
            "Quiz Points": t["quiz_points"],
            "Final Value": round(val + bonus),
        })
    df = pd.DataFrame(rows).sort_values(sort_by, ascending=False).reset_index(drop=True)
    df.index = df.index + 1
    return df


def update_rank_movement(store):
    """Call right after applying a round's results so the scoreboard can
    show who moved up/down since the previous round."""
    df = compute_leaderboard(store)
    new_ranks = {row["Team"]: rank for rank, row in df.iterrows()}
    prev = store.get("last_rank_snapshot", {})
    store["rank_movement"] = {name: prev.get(name, rank) - rank
                               for name, rank in new_ranks.items()}
    store["last_rank_snapshot"] = new_ranks


def render_scoreboard(store, title="\U0001F3C6 Live Scoreboard", apply_quiz_bonus=False,
                       interactive=False):
    st.markdown(f"### {title}")
    sort_by = "Final Value"
    if interactive:
        sort_by = st.radio("Sort by", ["Final Value", "Quiz Points"],
                            horizontal=True, key=f"sortby_{title}")
    df = compute_leaderboard(store, apply_quiz_bonus=apply_quiz_bonus, sort_by=sort_by)
    if df.empty:
        st.info("No teams have joined yet.")
        return
    max_val = max(df["Final Value"].max(), 1)
    movement = store.get("rank_movement", {})
    for rank, row in df.iterrows():
        name = row["Team"]
        pct_width = max(6, int(row["Final Value"] / max_val * 100))
        cls = "score-row score-rank1" if rank == 1 else "score-row"
        medal = {1: "\U0001F947", 2: "\U0001F948", 3: "\U0001F949"}.get(rank, f"#{rank}")
        mv = movement.get(name, 0)
        if mv > 0:
            arrow = f"<span style='color:#63e6a3;font-weight:700;'>\u25B2 {mv}</span>"
        elif mv < 0:
            arrow = f"<span style='color:#ff6b6b;font-weight:700;'>\u25BC {abs(mv)}</span>"
        else:
            arrow = "<span style='color:#7d8aa8;'>\u2013</span>"
        st.markdown(f"""
        <div class='{cls}'>
          <div style='flex:1;'>
            <div><b>{medal} {name}</b> &nbsp;{arrow}
              <span class='badge'>Quiz: {row['Quiz Points']} pts</span></div>
            <div class='bar-track'><div class='bar-fill' style='width:{pct_width}%;'></div></div>
          </div>
          <div style='font-size:1.15rem;font-weight:700;margin-left:16px;white-space:nowrap;'>
            \u20B9{row['Final Value']:,.0f}</div>
        </div>
        """, unsafe_allow_html=True)


# ----------------------------------------------------------------------
# ALLOCATION / REBALANCE FORM
# ----------------------------------------------------------------------
def allocation_form(team, key_prefix, button_label):
    """Nifty / Gold / Bitcoin / FD are set directly (slider OR typed ₹
    amount — a toggle switches the input widget, and both read/write the
    SAME underlying value so switching never loses an edit). Cash is
    always the auto-computed leftover, which is what guarantees the
    total is exact every single time — there is nothing to add up."""
    total = total_value(team)
    min_amt, max_amt = bounds_for(total)
    current = team["assets"] or {k: total / len(ASSET_KEYS) for k in ASSET_KEYS}

    pend_key = f"_pend_{key_prefix}"
    if pend_key not in st.session_state:
        st.session_state[pend_key] = {
            k: int(min(max(round(current.get(k, total / 5)), min_amt), max_amt))
            for k in ADJUSTABLE_KEYS
        }
    pend = st.session_state[pend_key]

    mode = st.radio("Adjust using", ["\U0001F39A\uFE0F Slider", "\U0001F522 Type an exact amount"],
                     horizontal=True, key=f"{key_prefix}_mode")
    st.caption(f"Nifty, Gold, Bitcoin and FD must each be between "
               f"\u20B9{min_amt:,} and \u20B9{max_amt:,}. Cash automatically "
               f"takes whatever's left, so your total is always exactly "
               f"\u20B9{total:,.0f}.")

    cols = st.columns(4)
    for i, key in enumerate(ADJUSTABLE_KEYS):
        with cols[i]:
            default_val = int(min(max(pend.get(key, min_amt), min_amt), max_amt))
            if mode.startswith("\U0001F39A"):
                val = st.slider(ASSET_LABEL[key], min_amt, max_amt, default_val,
                                 step=500, key=f"{key_prefix}_s_{key}")
            else:
                val = st.number_input(ASSET_LABEL[key], min_value=min_amt,
                                       max_value=max_amt, value=default_val,
                                       step=500, key=f"{key_prefix}_n_{key}")
            pend[key] = int(val)
    st.session_state[pend_key] = pend

    cash_amt = total - sum(pend.values())
    ok = min_amt <= cash_amt <= max_amt
    c1, c2 = st.columns([2, 1])
    with c1:
        st.metric("\U0001F4B5 Cash (auto-calculated)", f"\u20B9{cash_amt:,.0f}")
    with c2:
        st.metric("Total (always exact)", f"\u20B9{total:,.0f}")

    if not ok:
        st.warning(f"That leaves Cash at \u20B9{cash_amt:,.0f}, which is outside the "
                   f"\u20B9{min_amt:,}\u2013\u20B9{max_amt:,} range. Adjust the other "
                   f"four assets so Cash lands back in range.")
        st.button(button_label, disabled=True, use_container_width=True,
                  key=f"{key_prefix}_btn_disabled")
        return None

    st.success("Looks good \u2713")
    if st.button(button_label, type="primary", use_container_width=True,
                key=f"{key_prefix}_btn"):
        final = {k: float(pend[k]) for k in ADJUSTABLE_KEYS}
        final[AUTO_KEY] = float(cash_amt)
        del st.session_state[pend_key]
        return final
    return None


def apply_growth(team, pct_by_asset, round_idx, round_name):
    if team["assets"] is None:
        return
    for key, pct in pct_by_asset.items():
        team["assets"][key] = team["assets"][key] * (1 + pct / 100.0)
    team["value_history"].append((round_idx, round_name, total_value(team)))


# ----------------------------------------------------------------------
# CONTROLLER PAGE
# ----------------------------------------------------------------------
def page_controller(store):
    st.markdown("## \U0001F39B\uFE0F Control Room")
    with st.sidebar:
        if st.button("\U0001F504 Refresh now"):
            st.rerun()

    cur = store["current_round"]
    rd = round_def(cur)
    rstate = store["rounds"][cur]

    tabs = st.tabs(["\U0001F3AF Run the Game", "\U0001F9E0 Quiz Points",
                     "\U0001F465 Teams", "\U0001F3C6 Scoreboard"])

    # ---- Run the Game -------------------------------------------------
    with tabs[0]:
        st.markdown(f"#### Current Stage: **{stage_label(cur)}**")
        teams = store["teams"]
        n_teams = len(teams)

        if cur == 0:
            n_submitted = sum(1 for t in teams.values() if 0 in t["submitted_rounds"])
            st.progress(0 if n_teams == 0 else n_submitted / max(n_teams, 1),
                        text=f"{n_submitted}/{n_teams} teams have submitted their initial allocation")
            st.info("Allocation round is open. Teams are splitting their \u20B91,00,000 right now.")
            if st.button("\u25B6\uFE0F Close Allocation & Start Round 1", type="primary",
                         disabled=n_teams == 0):
                with get_lock():
                    store["current_round"] = 1
                st.rerun()

        elif 1 <= cur <= N_NEWS_ROUNDS:
            st.markdown(f"<div class='headline-card'><h3>Round {cur} of {N_NEWS_ROUNDS} \u2014 headline</h3></div>",
                        unsafe_allow_html=True)
            headline = st.text_area("Headline shown to teams", value=rstate["headline"], height=70)
            facilitator_note = st.text_input("Facilitator-only note (not shown to teams)",
                                              value=rstate["note"] or "")
            colA, colB = st.columns(2)
            with colA:
                if not rstate["released"]:
                    if st.button("\U0001F4E2 Release Headline to All Teams", type="primary",
                                 use_container_width=True):
                        with get_lock():
                            rstate["headline"] = headline
                            rstate["note"] = facilitator_note
                            rstate["released"] = True
                        st.rerun()
                else:
                    st.success("Headline is LIVE on team screens.")
            with colB:
                n_locked = sum(1 for t in teams.values() if cur in t["submitted_rounds"])
                st.metric("Teams locked in", f"{n_locked}/{n_teams}")

            if rstate["released"] and not rstate["results_applied"]:
                st.markdown("##### Enter the actual market move for this round")
                pct = rstate["price_pct"] or {}
                cols = st.columns(5)
                new_pct = {}
                for i, key in enumerate(ASSET_KEYS):
                    with cols[i]:
                        new_pct[key] = st.number_input(
                            ASSET_LABEL[key], value=float(pct.get(key, 0.0)),
                            step=0.5, format="%.2f", key=f"pct_{cur}_{key}")
                if st.button("\u2705 Apply Round Results", type="primary"):
                    with get_lock():
                        rstate["price_pct"] = new_pct
                        for t in teams.values():
                            apply_growth(t, new_pct, cur, rd["name"])
                        rstate["results_applied"] = True
                        update_rank_movement(store)
                    st.rerun()

            if rstate["results_applied"]:
                st.success(f"Round {cur} results applied.")
                render_scoreboard(store, title="Standings after this round", interactive=True)
                if cur < N_NEWS_ROUNDS:
                    if st.button(f"\u25B6\uFE0F Proceed to Round {cur+1}", type="primary"):
                        with get_lock():
                            store["current_round"] = cur + 1
                        st.rerun()
                else:
                    if st.button("\u25B6\uFE0F Proceed to The Reveal", type="primary"):
                        with get_lock():
                            store["current_round"] = REVEAL_IDX
                        st.rerun()

        elif cur == REVEAL_IDX:
            st.markdown("#### \U0001F31F The Reveal")
            if not store["revealed"]:
                st.write("Pull up real current prices for Nifty 50, Gold and Bitcoin, "
                         "then enter the final % move since the game started (or since "
                         "the last round, if you've been updating live).")
                pct = rstate["price_pct"] or {}
                cols = st.columns(5)
                new_pct = {}
                for i, key in enumerate(ASSET_KEYS):
                    with cols[i]:
                        new_pct[key] = st.number_input(
                            ASSET_LABEL[key], value=float(pct.get(key, 0.0)),
                            step=0.5, format="%.2f", key=f"pct_reveal_{key}")
                if st.button("\U0001F3C1 Calculate Final Results & Reveal Winner",
                             type="primary", use_container_width=True):
                    with get_lock():
                        rstate["price_pct"] = new_pct
                        for t in store["teams"].values():
                            apply_growth(t, new_pct, REVEAL_IDX, "The Reveal")
                        rstate["results_applied"] = True
                        store["revealed"] = True
                        update_rank_movement(store)
                    st.rerun()
            else:
                st.balloons()
                render_scoreboard(store, title="\U0001F3C6 FINAL RESULTS (quiz bonus applied)",
                                   apply_quiz_bonus=True, interactive=True)
                st.divider()
                st.markdown("#### \U0001F504 Ready for another round?")
                st.caption("Keeps the same registered teams, wipes every score and portfolio, "
                           "and returns everyone to the Allocation Round.")
                confirm_new = st.checkbox("Yes, start a new game", key="confirm_new_game")
                if st.button("\U0001F195 Start New Game", type="primary", disabled=not confirm_new):
                    with get_lock():
                        reset_scores_keep_teams(store)
                    st.rerun()

    # ---- Quiz points ----------------------------------------------------
    with tabs[1]:
        st.markdown("#### Award quiz points live")
        st.caption("1 question per news round \u00b7 first correct buzz gets the point \u00b7 "
                   "use the reference list below (10 provided, use 8).")
        teams = store["teams"]
        if not teams:
            st.info("No teams yet.")
        else:
            cols = st.columns(len(teams))
            for c, (name, t) in zip(cols, teams.items()):
                with c:
                    st.metric(name, t["quiz_points"])
                    if st.button("+1", key=f"quiz_{name}"):
                        with get_lock():
                            t["quiz_points"] += 1
                        st.rerun()
                    if st.button("-1", key=f"quizm_{name}"):
                        with get_lock():
                            t["quiz_points"] = max(0, t["quiz_points"] - 1)
                        st.rerun()
        with st.expander("\U0001F4CB Sample quiz questions & answers"):
            for i, (q, a) in enumerate(QUIZ_QUESTIONS, 1):
                st.markdown(f"**{i}. {q}**  \n_{a}_")

    # ---- Teams management ----------------------------------------------
    with tabs[2]:
        st.markdown("#### Registered teams")
        new_name = st.text_input("Add a team name")
        if st.button("Add team") and new_name.strip():
            with get_lock():
                if new_name not in store["registered_teams"]:
                    store["registered_teams"].append(new_name.strip())
            st.rerun()
        for name in store["registered_teams"]:
            joined = "\u2705 joined" if name in store["teams"] else "\u2014 not joined yet"
            st.write(f"**{name}** — {joined}")

        st.divider()
        with st.expander("\u26A0\uFE0F Danger zone"):
            st.caption("Full reset: wipes teams, scores and the team roster, back to the "
                       "5 default team names. Use the green **Start New Game** button on "
                       "the Reveal screen instead if you just want to replay with the same "
                       "teams.")
            confirm = st.checkbox("I understand this resets everything, including the team list")
            if st.button("\U0001F5D1\uFE0F Full Reset", disabled=not confirm):
                with get_lock():
                    fresh = build_store()
                    for k in list(store.keys()):
                        del store[k]
                    store.update(fresh)
                st.rerun()

    # ---- Scoreboard tab ---------------------------------------------------
    with tabs[3]:
        render_scoreboard(store, apply_quiz_bonus=store["revealed"], interactive=True)
        if store["teams"]:
            st.markdown("##### Portfolio value over time")
            chart_df = pd.DataFrame({
                t["name"]: [h[2] for h in t["value_history"]] or [STARTING_VALUE]
                for t in store["teams"].values()
            })
            st.line_chart(chart_df)


# ----------------------------------------------------------------------
# TEAM PAGE
# ----------------------------------------------------------------------
def page_team(store, team_name):
    with get_lock():
        if team_name not in store["teams"]:
            store["teams"][team_name] = new_team(team_name)
        team = store["teams"][team_name]

    st.markdown(f"## \U0001F3AE {team_name}")
    val = total_value(team)
    cur = store["current_round"]
    if cur == 0:
        round_display = "Allocation"
    elif cur == REVEAL_IDX:
        round_display = "Reveal"
    else:
        round_display = f"{cur} / {N_NEWS_ROUNDS}"
    c1, c2, c3 = st.columns(3)
    c1.metric("Portfolio Value", f"\u20B9{val:,.0f}")
    c2.metric("Quiz Points", team["quiz_points"])
    c3.metric("Round", round_display)

    rd = round_def(cur)
    rstate = store["rounds"][cur]

    st.divider()

    if cur == 0:
        if 0 in team["submitted_rounds"]:
            auto_refresh(4)
            st.markdown("<div class='waiting-box pulse'>\u2705 Allocation locked in.<br>"
                        "Waiting for the controller to start Round 1\u2026</div>",
                        unsafe_allow_html=True)
        else:
            st.markdown("### Split your \u20B91,00,000")
            new_amt = allocation_form(team, "alloc0", "\U0001F512 Lock In Initial Allocation")
            if new_amt is not None:
                with get_lock():
                    team["assets"] = new_amt
                    team["submitted_rounds"].add(0)
                    team["value_history"].append((0, "Allocation Round", STARTING_VALUE))
                st.rerun()

    elif 1 <= cur <= N_NEWS_ROUNDS:
        showing_form = False
        if not rstate["released"]:
            auto_refresh(4)
            st.markdown(f"<div class='waiting-box pulse'>\u23F3 Waiting for the controller "
                        f"to release the Round {cur} headline\u2026</div>",
                        unsafe_allow_html=True)
        else:
            st.markdown(f"<div class='headline-card'><h2>\U0001F4F0 {rd['name']}</h2>"
                        f"<div class='headline-text'>\u201c{rstate['headline']}\u201d</div></div>",
                        unsafe_allow_html=True)
            if cur in team["submitted_rounds"]:
                st.markdown("<div class='waiting-box pulse'>\u2705 Locked in for this round.<br>"
                            "Waiting for other teams / the controller\u2026</div>",
                            unsafe_allow_html=True)
            elif rstate["results_applied"]:
                st.info("The controller already applied this round's results before you "
                        "locked in \u2014 your last allocation was carried forward automatically.")
            else:
                showing_form = True
                st.markdown("##### Your current holdings")
                if team["assets"]:
                    holdings = pd.DataFrame({
                        "Asset": [ASSET_LABEL[k] for k in ASSET_KEYS],
                        "Value (\u20B9)": [round(team["assets"][k]) for k in ASSET_KEYS],
                    })
                    st.dataframe(holdings, hide_index=True, use_container_width=True)
                st.markdown("##### Rebalance (or leave as-is) and lock in")
                new_amt = allocation_form(team, f"alloc{cur}", "\U0001F512 Lock In This Round")
                if new_amt is not None:
                    with get_lock():
                        team["assets"] = new_amt
                        team["submitted_rounds"].add(cur)
                    st.rerun()

            if rstate["results_applied"]:
                st.divider()
                prev_val = STARTING_VALUE
                hist = team["value_history"]
                if len(hist) >= 2:
                    prev_val = hist[-2][2]
                new_val = total_value(team)
                delta = new_val - prev_val
                st.metric(f"Portfolio value after {rd['name']}", f"\u20B9{new_val:,.0f}",
                          delta=f"\u20B9{delta:,.0f}")
                st.caption("Waiting for the controller to move to the next round\u2026")

        if not showing_form:
            auto_refresh(4)

    elif cur == REVEAL_IDX:
        if not store["revealed"]:
            auto_refresh(5)
            st.markdown("<div class='waiting-box pulse'>\U0001F31F The Reveal is coming up. "
                        "Sit tight\u2026</div>", unsafe_allow_html=True)
        else:
            st.balloons()
            df = compute_leaderboard(store, apply_quiz_bonus=True)
            rank = int(df.index[df["Team"] == team_name][0])
            st.markdown(f"## Final Rank: #{rank}")
            render_scoreboard(store, title="\U0001F3C6 Final Results", apply_quiz_bonus=True)

    st.divider()
    with st.expander("\U0001F3C6 Live Scoreboard"):
        render_scoreboard(store, apply_quiz_bonus=store["revealed"])
    st.button("\U0001F504 Refresh now")


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Build a Portfolio, Survive the News",
                       page_icon="\U0001F4C8", layout="wide")
    inject_css()
    store = get_store()
    restore_session()

    with st.sidebar:
        st.markdown("### \U0001F4C8 Survive the News")
        if st.session_state.role:
            who = ("Controller" if st.session_state.role == "controller"
                   else st.session_state.get("team_name", ""))
            st.write(f"Logged in as **{who}**")
            if st.button("Log out"):
                logout()
                st.rerun()

    if st.session_state.role is None:
        page_login(store)
    elif st.session_state.role == "controller":
        page_controller(store)
    elif st.session_state.role == "team":
        page_team(store, st.session_state.team_name)


if __name__ == "__main__":
    main()
