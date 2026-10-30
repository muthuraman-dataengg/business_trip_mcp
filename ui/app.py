"""Business Trip Copilot — Streamlit UI.

    streamlit run ui/app.py

Every action goes through MCP (via CopilotHost): the UI never touches the
database or external APIs directly.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from copilot.config import get_settings  # noqa: E402
from copilot.host.orchestrator import CopilotHost, ToolCallError, TripRequest  # noqa: E402
from copilot.models import TravelerPreferences  # noqa: E402

st.set_page_config(page_title="Trip Copilot", page_icon="✈", layout="wide")

# --------------------------------------------------------------------- style
st.markdown(
    """
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;700&family=Instrument+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--ink:#1B2A41;--paper:#F4F6F9;--sky:#2F6FDE;--amber:#E8A33D;--green:#2E8B57;--alert:#C8553D;--line:#D9DFE8;}
html, body, [class*="css"], .stMarkdown, .stTextInput, .stButton button {font-family:'Instrument Sans',system-ui,sans-serif;}
h1,h2,h3{color:var(--ink);letter-spacing:-0.01em;}
.block-container{padding-top:2rem;max-width:1200px;}
.pass{display:grid;grid-template-columns:1fr auto;border:1px solid var(--line);border-radius:14px;background:#fff;overflow:hidden;margin:0.5rem 0 1.25rem;}
.pass-main{padding:1.1rem 1.4rem;}
.pass-route{overflow-wrap:anywhere;font-family:'Barlow Condensed','Arial Narrow','Roboto Condensed',sans-serif;font-stretch:condensed;font-weight:700;font-size:clamp(2rem,5vw,3.4rem);line-height:1;color:var(--ink);text-transform:uppercase;letter-spacing:0.02em;}
.pass-route .plane{color:var(--sky);font-size:0.7em;padding:0 0.35em;vertical-align:0.12em;}
.pass-meta{display:flex;flex-wrap:wrap;gap:1.6rem;margin-top:0.8rem;color:#4A5A72;font-size:0.92rem;}
.pass-meta b{display:block;color:var(--ink);font-size:1.02rem;font-weight:600;}
.pass-stub{border-left:2px dashed var(--line);padding:1.1rem 1.4rem;min-width:170px;display:flex;flex-direction:column;justify-content:center;font-family:'Barlow Condensed',sans-serif;}
.pass-stub .k{color:#6B7A90;font-size:0.95rem;}
.pass-stub .v{font-size:1.6rem;font-weight:700;color:var(--ink);}
.chip{display:inline-block;padding:0.12rem 0.6rem;border-radius:999px;font-size:0.82rem;font-weight:600;}
.chip.pending{background:#FCEFD9;color:#8A5A10;}
.chip.ok{background:#DFF1E6;color:#1F6A41;}
.chip.bad{background:#F7E1DB;color:#8E3523;}
.chip.sim{background:#E7ECF4;color:#3A4A63;}
.step{font-size:0.9rem;color:#33445C;padding:0.15rem 0;}
.step code{background:#EEF2F8;color:var(--ink);}
@media (max-width:640px){.pass{grid-template-columns:1fr}.pass-stub{border-left:0;border-top:2px dashed var(--line);}}
</style>
""",
    unsafe_allow_html=True,
)


# -------------------------------------------------------------------- helpers
def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


def settings():  # noqa: ANN201
    base = get_settings()
    mode = st.session_state.get("transport", base.mcp_transport)
    return base.model_copy(update={"mcp_transport": mode})


async def _with_host(fn, servers=("travel", "policy", "work")):  # noqa: ANN001, ANN202
    async with CopilotHost(settings(), servers=servers) as host:
        return await fn(host)


def mcp(fn, servers=("travel", "policy", "work")):  # noqa: ANN001, ANN201
    """Run ``fn(host)`` inside a short-lived MCP session."""
    return run(_with_host(fn, servers))


async def _inbox(h: CopilotHost) -> tuple:
    return await h.read_json("travel", "travel://travelers"), await h.pending_requests()


def refresh_travelers() -> None:
    """Travelers + the shared approver inbox, in one MCP round trip per rerun."""
    st.session_state.travelers, st.session_state.pending = mcp(_inbox, ("travel",))


LABELS = {
    "id": "Reference", "hotel_name": "Hotel", "city": "City", "check_in": "Check-in", "nights": "Nights",
    "approved_by": "Approved by", "status": "Status", "airline": "Airline", "departs_at": "Departs",
    "duration_hours": "Hours", "stops": "Stops", "total_price": "Price", "currency": "Currency",
    "name": "Hotel", "stars": "Stars", "distance_to_centre_km": "Km to centre",
    "indicative_nightly_usd": "Indicative USD/night", "title": "Task", "due_date": "Due",
}


def table(rows: List[Dict[str, Any]], cols: List[str]) -> None:
    df = pd.DataFrame(rows)
    df = df[[c for c in cols if c in df.columns]].rename(columns=LABELS)
    st.dataframe(df, hide_index=True, use_container_width=True)


def chip(text: str, kind: str) -> str:
    return f'<span class="chip {kind}">{text}</span>'


def boarding_pass(req: TripRequest, traveler: Dict[str, Any]) -> None:
    prefs = traveler.get("preferences", {})
    st.markdown(
        f"""
<div class="pass" role="img" aria-label="Trip {req.origin} to {req.destination}">
  <div class="pass-main">
    <div class="pass-route">{req.origin}<span class="plane">✈</span>{req.destination}</div>
    <div class="pass-meta">
      <span>Traveler<b>{traveler['name']}</b></span>
      <span>Depart<b>{req.start_date:%a %d %b %Y}</b></span>
      <span>Return<b>{req.end_date:%a %d %b %Y}</b></span>
      <span>Purpose<b>{req.purpose}</b></span>
    </div>
  </div>
  <div class="pass-stub">
    <span class="k">Nights</span><span class="v">{req.nights}</span>
    <span class="k">Cabin · seat</span><span class="v">{prefs.get('cabin_class','economy').replace('_',' ')} · {prefs.get('seat','any')}</span>
  </div>
</div>""",
        unsafe_allow_html=True,
    )


def preferences_form(prefs: TravelerPreferences, key: str) -> TravelerPreferences:
    c1, c2, c3 = st.columns(3)
    with c1:
        cabin = st.selectbox("Cabin class", ["economy", "premium_economy", "business", "first"],
                             index=["economy", "premium_economy", "business", "first"].index(prefs.cabin_class), key=f"{key}cabin")
        seat = st.selectbox("Seat", ["window", "aisle", "any"], index=["window", "aisle", "any"].index(prefs.seat), key=f"{key}seat")
        max_stops = st.number_input("Max stops", 0, 3, prefs.max_stops, key=f"{key}stops")
    with c2:
        stars = st.slider("Hotel stars", 1, 5, (prefs.hotel_min_stars, prefs.hotel_max_stars), key=f"{key}stars")
        airlines = st.text_input("Preferred airlines", ", ".join(prefs.preferred_airlines), key=f"{key}air",
                                 help="Comma separated")
        avoid = st.text_input("Airlines to avoid", ", ".join(prefs.avoided_airlines), key=f"{key}avoid")
    with c3:
        loyalty = st.text_input("Loyalty programmes", ", ".join(prefs.loyalty_programs), key=f"{key}loy")
        dietary = st.text_input("Dietary needs", prefs.dietary, key=f"{key}diet")
        style = st.text_input("Trip style", prefs.trip_style, key=f"{key}style",
                              placeholder="e.g. quiet hotels, early flights")
    notes = st.text_area("Anything else the copilot should know", prefs.notes, key=f"{key}notes", height=70)

    def split(s: str) -> List[str]:
        return [x.strip() for x in s.split(",") if x.strip()]

    return TravelerPreferences(
        preferred_airlines=split(airlines), avoided_airlines=split(avoid), seat=seat, cabin_class=cabin,
        hotel_min_stars=stars[0], hotel_max_stars=stars[1], max_stops=int(max_stops), dietary=dietary,
        loyalty_programs=split(loyalty), trip_style=style, notes=notes,
    )


def traveler_form(existing: Dict[str, Any] | None = None) -> None:
    prefix = "edit" if existing else "new"
    with st.form(f"{prefix}_traveler"):
        c1, c2, c3, c4 = st.columns([2, 2, 1, 2])
        name = c1.text_input("Full name", existing["name"] if existing else "")
        home = c2.text_input("Home city", existing["home_city"] if existing else "")
        cur = c3.text_input("Currency", existing["home_currency"] if existing else "USD", max_chars=3)
        dept = c4.text_input("Department", existing.get("department", "") if existing else "")
        email = st.text_input("Email (optional)", (existing or {}).get("email") or "")
        st.markdown("**Travel preferences**")
        prefs = preferences_form(TravelerPreferences(**(existing or {}).get("preferences", {})), prefix)
        if st.form_submit_button("Save profile" if existing else "Create profile", type="primary"):
            if not name.strip() or not home.strip():
                st.error("Name and home city are required.")
                return
            args = {"name": name, "home_city": home, "home_currency": cur.upper(), "department": dept,
                    "email": email or None, "preferences": prefs.model_dump()}
            try:
                if existing:
                    mcp(lambda h: h.call("travel", "update_traveler", {"traveler_id": existing["id"], **args}), ("travel",))
                    st.toast("Profile saved")
                else:
                    t = mcp(lambda h: h.call("travel", "create_traveler", args), ("travel",))
                    st.session_state.select_traveler = t["id"]
                    st.toast(f"Profile created for {t['name']}")
                st.session_state.pop("traveler_cache", None)
                refresh_travelers()
                st.rerun()
            except ToolCallError as exc:
                st.error(f"Could not save the profile: {exc}")


def load_traveler(tid: str) -> Dict[str, Any]:
    cache = st.session_state.setdefault("traveler_cache", {})
    if tid not in cache:
        cache[tid] = mcp(lambda h: h.read_json("travel", f"travel://travelers/{tid}/profile"), ("travel",))
    return cache[tid]


# ------------------------------------------------------------------- sidebar
base = get_settings()
st.session_state.setdefault("transport", base.mcp_transport)

with st.sidebar:
    st.markdown("### ✈ Trip Copilot")
    st.selectbox(
        "MCP connection",
        ["inprocess", "stdio", "http"],
        key="transport",
        help="inprocess: fastest for local use · stdio: host launches servers · http: connect to running servers",
    )
    if st.session_state.transport == "http":
        st.caption(f"{base.travel_mcp_url}\n\n{base.policy_mcp_url}\n\n{base.work_mcp_url}")
    try:
        refresh_travelers()
        connected = True
    except Exception as exc:  # noqa: BLE001
        connected = False
        st.error(f"Can't reach the MCP servers ({type(exc).__name__}). "
                 "For http mode run `python scripts/start_servers.py` first, or switch to inprocess.")
    travelers = st.session_state.get("travelers", []) if connected else []

    if travelers:
        ids = [t["id"] for t in travelers]
        wanted = st.session_state.pop("select_traveler", None)
        if wanted in ids:
            st.session_state.traveler_id = wanted
        elif st.session_state.get("traveler_id") not in ids:
            st.session_state.traveler_id = ids[0]
        st.selectbox(
            "Traveler", ids, key="traveler_id",
            format_func=lambda i: next(f"{t['name']} — {t['home_city']}" for t in travelers if t["id"] == i),
        )
    st.caption(f"Model: `{base.llm_model}`")
    pending = len(st.session_state.get("pending", [])) if connected else 0
    if pending:
        st.markdown(chip(f"{pending} booking{'s' if pending > 1 else ''} awaiting approval", "pending"), unsafe_allow_html=True)

if not connected:
    st.stop()

# ---------------------------------------------------------------- onboarding
if not travelers:
    st.title("Set up your first traveler")
    st.write("Preferences live in each traveler's profile, so the copilot plans around them. "
             "Create a profile to start planning.")
    traveler_form()
    st.stop()

traveler = load_traveler(st.session_state.traveler_id)
tab_plan, tab_approve, tab_tasks, tab_profile, tab_mcp = st.tabs(
    # Labels must stay constant: Streamlit resets the active tab when a label changes.
    ["Plan a trip", "Approvals", "Tasks", "Traveler profile", "MCP inspector"]
)

# ------------------------------------------------------------------ plan tab
with tab_plan:
    rules = st.session_state.get("rules")
    if rules is None:
        rules = st.session_state["rules"] = mcp(lambda h: h.read_json("policy", "policy://rules"), ("policy",))
    lead = rules["booking"]["advance_days_recommended"]

    with st.form("trip"):
        c1, c2 = st.columns(2)
        origin = c1.text_input("From", traveler["home_city"])
        destination = c2.text_input("To", placeholder="City, e.g. Singapore")
        c3, c4, c5 = st.columns([1, 1, 2])
        start = c3.date_input("Depart", date.today() + timedelta(days=lead), min_value=date.today())
        end = c4.date_input("Return", date.today() + timedelta(days=lead + 4), min_value=date.today())
        purpose = c5.text_input("Purpose", "Client meetings")
        extra = st.text_input("Extra instructions (optional)", placeholder="e.g. arrive a day early, hotel near DIFC")
        go = st.form_submit_button("Plan this trip", type="primary")
    st.caption(f"Company policy recommends booking at least {lead} days ahead.")

    if go:
        try:
            req = TripRequest(traveler["id"], origin.strip(), destination.strip(), start, end, purpose, extra)
            if not destination.strip():
                raise ValueError("Enter a destination city.")
        except ValueError as exc:
            st.error(str(exc))
        else:
            boarding_pass(req, traveler)
            status = st.status("Planning with your MCP tools…", expanded=True)
            final_slot = st.empty()

            async def plan(host: CopilotHost) -> Dict[str, Any]:
                final, error = "", None
                async for ev in host.plan_trip(req):
                    if ev.kind == "context":
                        status.markdown(f'<div class="step">📚 {ev.title}</div>', unsafe_allow_html=True)
                    elif ev.kind == "tool_call":
                        args = ", ".join(f"{k}={v}" for k, v in ev.data.items())
                        status.markdown(f'<div class="step">🔧 <code>{ev.title}</code> {args}</div>', unsafe_allow_html=True)
                    elif ev.kind == "tool_result" and ev.data["is_error"]:
                        status.markdown(f'<div class="step">⚠️ <code>{ev.title}</code> {ev.data["result"]}</div>', unsafe_allow_html=True)
                    elif ev.kind == "approval_request":
                        status.markdown(f'<div class="step">🟠 Hotel sent for approval: {ev.data["hotel_name"]}</div>', unsafe_allow_html=True)
                    elif ev.kind == "error":
                        error = f"{ev.title}: {ev.data}"
                    elif ev.kind == "final":
                        final = ev.data
                return {"final": final, "error": error, "results": host.tool_results, "proposals": host.proposals}

            try:
                out = mcp(plan)
            except Exception as exc:  # noqa: BLE001
                out = {"final": "", "error": f"{type(exc).__name__}: {exc}", "results": {}, "proposals": []}
            if out["error"]:
                status.update(label="Planning stopped", state="error")
                st.error(out["error"])
            else:
                status.update(label="Plan ready", state="complete", expanded=False)
            st.session_state.last = {"req": req, "out": out}

    last = st.session_state.get("last")
    if last and last["out"]["final"]:
        if not go:
            boarding_pass(last["req"], traveler)
        out, res = last["out"], last["out"]["results"]
        if out["proposals"]:
            st.info("A hotel request is waiting in the Approvals tab. Nothing has been booked yet.")
        left, right = st.columns([3, 2], gap="large")
        with left:
            st.markdown(out["final"])
        with right:
            fc = (res.get("travel_get_trip_forecast") or [None])[-1]
            if fc and fc.get("available"):
                st.markdown("**Forecast**")
                import altair as alt

                df = pd.DataFrame(fc["days"]).melt(id_vars=["date"], value_vars=["max_c", "min_c"], var_name="kind", value_name="°C")
                df["kind"] = df["kind"].map({"max_c": "High", "min_c": "Low"})
                chart = (
                    alt.Chart(df).mark_line(point=True)
                    .encode(
                        x=alt.X("date:T", title=None, axis=alt.Axis(format="%a %d")),
                        y=alt.Y("°C:Q", scale=alt.Scale(zero=False)),
                        color=alt.Color("kind:N", scale=alt.Scale(range=["#C8553D", "#2F6FDE"]), legend=alt.Legend(title=None, orient="top")),
                        tooltip=["date:T", "kind:N", "°C:Q"],
                    ).properties(height=190)
                )
                st.altair_chart(chart, use_container_width=True)
            for flights in res.get("travel_search_flights", []):
                offers = flights.get("offers", [])
                if offers:
                    o = offers[0]
                    label = f"**Flights {o['origin']} → {o['destination']}** " + (
                        chip("simulated fares", "sim") if flights.get("provider") == "simulated" else chip("live fares", "ok"))
                    st.markdown(label, unsafe_allow_html=True)
                    table(offers, ["airline", "departs_at", "duration_hours", "stops", "total_price", "currency"])
            hs = (res.get("travel_search_hotels") or [None])[-1]
            if hs and hs.get("hotels"):
                st.markdown("**Hotels nearby** " + chip("indicative prices", "sim"), unsafe_allow_html=True)
                table(hs["hotels"], ["name", "stars", "distance_to_centre_km", "indicative_nightly_usd"])
            comp = (res.get("policy_check_trip_compliance") or [None])[-1]
            if comp:
                st.markdown("**Policy check** " + (chip("compliant", "ok") if comp["compliant"] else chip("needs approval", "bad")),
                            unsafe_allow_html=True)
                for issue in comp["issues"]:
                    st.markdown(f"- {issue}")
                for a in comp["approvals_required"]:
                    st.markdown(f"- Requires: {a}")

# -------------------------------------------------------------- approvals tab
with tab_approve:
    st.subheader("Bookings awaiting a decision")
    st.caption("The copilot can only request bookings. A hotel is booked when someone approves it here — "
               "from any browser, since requests are stored by the Travel MCP server.")
    approver = st.text_input("Approving as", placeholder="Your name and role", key="approver")
    inbox = st.session_state.get("pending", [])
    if not inbox:
        st.write("Nothing waiting. Requests appear here after the copilot plans a trip.")
    for p in inbox:
        with st.container(border=True):
            st.markdown(f"**{p['hotel_name']}**, {p['city']} for {p.get('traveler_name') or p['traveler_id']} "
                        + chip("pending", "pending"), unsafe_allow_html=True)
            if p.get("reason"):
                st.caption(p["reason"])
            c1, c2, c3 = st.columns(3)
            check_in = c1.date_input("Check-in", date.fromisoformat(p["check_in"]), key=f"ci{p['id']}")
            nights = c2.number_input("Nights", 1, 60, int(p["nights"]), key=f"n{p['id']}")
            rate = p.get("nightly_rate")
            c3.markdown(f"Indicative rate<br><b>{rate:,.0f} {p.get('currency') or ''}</b> per night" if rate else "Indicative rate<br>—",
                        unsafe_allow_html=True)
            b1, b2, _ = st.columns([1, 1, 4])
            if b1.button("Approve booking", key=f"ok{p['id']}", type="primary", disabled=not approver.strip()):
                try:
                    booking = mcp(lambda h: h.approve_booking(p["id"], approver, check_in.isoformat(), int(nights)), ("travel",))
                    st.toast(f"Booking confirmed: {booking['id']}")
                    st.rerun()
                except (ToolCallError, ValueError) as exc:
                    st.error(f"Booking not confirmed: {exc}")
            if b2.button("Reject", key=f"no{p['id']}", disabled=not approver.strip()):
                try:
                    mcp(lambda h: h.reject_booking(p["id"], approver), ("travel",))
                    st.toast("Request rejected")
                    st.rerun()
                except ToolCallError as exc:
                    st.error(str(exc))
    if inbox and not approver.strip():
        st.caption("Enter who is deciding to enable the buttons.")

    st.subheader(f"Confirmed bookings for {traveler['name']}")
    bookings = mcp(lambda h: h.read_json("travel", f"travel://travelers/{traveler['id']}/bookings"), ("travel",))
    if bookings:
        table(bookings, ["id", "hotel_name", "city", "check_in", "nights", "approved_by", "status"])
    else:
        st.write("No bookings yet.")

# ------------------------------------------------------------------ tasks tab
with tab_tasks:
    tasks = mcp(lambda h: h.call("work", "list_trip_tasks", {"traveler_id": traveler["id"]}), ("work",))
    st.subheader(f"Trip tasks for {traveler['name']}")
    if tasks:
        df = pd.DataFrame(tasks)[["id", "title", "due_date", "status"]]
        edited = st.data_editor(
            df, hide_index=True, use_container_width=True, disabled=["id", "title", "due_date"], key="tasks_editor",
            column_config={
                "id": "Reference", "title": "Task", "due_date": "Due",
                "status": st.column_config.SelectboxColumn("Status", options=["open", "in_progress", "done", "cancelled"]),
            },
        )
        changes = [(r.id, r.status) for r, o in zip(edited.itertuples(), df.itertuples()) if r.status != o.status]
        if changes and st.button("Save status changes", type="primary"):
            async def save(h: CopilotHost) -> None:
                for tid, status_ in changes:
                    await h.call("work", "update_task_status", {"task_id": tid, "status": status_})
            mcp(save, ("work",))
            st.toast(f"Updated {len(changes)} task(s)")
            st.rerun()
    else:
        st.write("No tasks yet. The copilot creates preparation tasks when it plans a trip, or add one below.")
    with st.form("new_task", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        title = c1.text_input("New task")
        due = c2.date_input("Due", None)
        if st.form_submit_button("Add task") and title.strip():
            mcp(lambda h: h.call("work", "create_trip_task",
                                 {"title": title, "traveler_id": traveler["id"], "due_date": due.isoformat() if due else None}), ("work",))
            st.rerun()

# ---------------------------------------------------------------- profile tab
with tab_profile:
    st.subheader("Traveler profile")
    st.caption("The copilot reads this through the MCP resource "
               f"`travel://travelers/{traveler['id']}/profile` before every plan.")
    traveler_form(traveler)
    with st.expander("Add another traveler"):
        traveler_form()

# -------------------------------------------------------------- inspector tab
with tab_mcp:
    st.subheader("What each MCP server exposes")
    if st.button("Load server catalogue") or "catalogue" in st.session_state:
        if "catalogue" not in st.session_state:
            st.session_state.catalogue = mcp(lambda h: h.inspect())
        cat = st.session_state.catalogue
        for server, info in cat.items():
            with st.expander(f"{server} — {len(info['tools'])} tools, "
                             f"{len(info['resources']) + len(info['templates'])} resources, {len(info['prompts'])} prompts"):
                rows = [{"tool": t["name"],
                         "agent can use": "no" if (t["meta"].get("audience") == "host" or t["meta"].get("requires_approval")) else "yes",
                         "read only": t["annotations"].get("read_only_hint", ""),
                         "description": (t["description"] or "").split("\n")[0]} for t in info["tools"]]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                st.markdown("Resources: " + ", ".join(f"`{r['uri']}`" for r in info["resources"] + [
                    {"uri": t["uri_template"]} for t in info["templates"]]))
                if info["prompts"]:
                    st.markdown("Prompts: " + ", ".join(f"`{p['name']}({', '.join(p['arguments'])})`" for p in info["prompts"]))

        st.markdown("**Try a tool**")
        c1, c2 = st.columns([1, 2])
        server = c1.selectbox("Server", list(cat))
        tools = {t["name"]: t for t in cat[server]["tools"]}
        tool = c2.selectbox("Tool", list(tools))
        schema = tools[tool]["input_schema"]
        example = {k: v.get("default", "") for k, v in schema.get("properties", {}).items()}
        args_text = st.text_area("Arguments (JSON)", json.dumps(example, indent=2), key=f"args_{server}_{tool}", height=150)
        if tools[tool]["meta"].get("requires_approval"):
            st.warning("This tool has side effects and normally runs only after human approval.")
        if st.button("Call tool"):
            try:
                args = json.loads(args_text)
                st.json(mcp(lambda h: h.call(server, tool, args), (server,)))
            except json.JSONDecodeError as exc:
                st.error(f"Arguments aren't valid JSON: {exc}")
            except ToolCallError as exc:
                st.error(str(exc))
