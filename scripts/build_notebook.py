"""Generate notebooks/business_trip_copilot_walkthrough.ipynb (run: python scripts/build_notebook.py)."""

from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]
cells = []


def md(text: str) -> None:
    cells.append(nbf.v4.new_markdown_cell(text.strip()))


def code(text: str) -> None:
    cells.append(nbf.v4.new_code_cell(text.strip()))


md("""
# Business Trip Copilot — MCP, step by step

This notebook walks through the production project one concept at a time. Run the cells in order.

| Step | MCP concept | What you'll see |
|---|---|---|
| 1 | Setup & config | Settings come from `.env` — nothing hard-coded |
| 2 | MCP servers as objects | Connect in-memory with `mcp.Client` and list what each server exposes |
| 3 | Tools that write | Create a traveler profile (replaces the old hard-coded preferences) |
| 4 | Resources & resource templates | Read the profile, a live city guide, the policy summary |
| 5 | Prompts | Fetch the reusable planning prompt |
| 6 | Real-time tools | Weather, forecast, flights, hotels, FX, public holidays |
| 7 | MCP + RAG | Semantic policy search and an automated compliance check |
| 8 | Work tools | Persistent trip tasks |
| 9 | Multiple servers + MCPAdapter | Prefixed tools, and how the host decides what the agent may use |
| 10 | The agent | A LangChain agent plans the trip, streaming every tool call |
| 11 | Human approval | Nothing is booked until you approve |
| 12 | STDIO transport | The host launches servers as subprocesses |
| 13 | Streamable HTTP transport | Servers run independently, with bearer-token auth |
| 14 | Client config | Use the same servers from Claude Desktop, Cursor or VS Code |

**Before you start:** install the dependencies (`pip install -r requirements-dev.txt`) and put your `OPENAI_API_KEY` in `.env`.
No internet or key? Set `COPILOT_OFFLINE_DEMO=1` (or flip the flag in step 1) to use recorded API responses and a scripted model.
""")

md("## 1. Setup\n\nEdit the trip details below — they're the only inputs. Everything else comes from configuration.")
code("""
import os, sys, json, asyncio, warnings
from datetime import date, timedelta
from pathlib import Path

ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore", category=DeprecationWarning)

# ---- your trip (edit me) ----
TRAVELER_NAME = "Muthu Kannan"
HOME_CITY     = "Phoenix"
HOME_CURRENCY = "USD"
DESTINATION   = "Dubai"
START_DATE    = date.today() + timedelta(days=10)
END_DATE      = START_DATE + timedelta(days=4)
PURPOSE       = "Client workshop"

# Set to True to run without internet / API key (recorded responses + scripted model)
USE_OFFLINE_DEMO = os.getenv("COPILOT_OFFLINE_DEMO") == "1"
print("Project root:", ROOT, "| offline demo:", USE_OFFLINE_DEMO)
""")
code("""
from copilot.config import get_settings

settings = get_settings()
if USE_OFFLINE_DEMO:
    from tests.fakes import install_fake_http
    install_fake_http()

print("LLM model      :", settings.llm_model)
print("MCP transport  :", settings.mcp_transport)
print("Database       :", settings.db_path)
print("Policy docs    :", settings.policies_path)
print("Flights source :", "Duffel (live)" if settings.duffel_access_token else "simulator (set DUFFEL_ACCESS_TOKEN for live fares)")
print("OpenAI key set :", bool(settings.openai_api_key or os.getenv("OPENAI_API_KEY")))
""")

md("""
## 2. MCP servers are ordinary Python objects

Each server (`travel`, `policy`, `work`) is an `MCPServer`. `mcp.Client` can connect to one **in memory** — no subprocess,
no network — which is perfect for exploring and testing. Later steps use STDIO and HTTP for the same servers.
""")
code("""
from mcp import Client
from copilot.servers.travel import server as travel_server
from copilot.servers.policy import server as policy_server
from copilot.servers.work import server as work_server

SERVERS = {"travel": travel_server, "policy": policy_server, "work": work_server}

for name, srv in SERVERS.items():
    async with Client(srv) as c:
        tools = (await c.list_tools()).tools
        resources = (await c.list_resources()).resources
        templates = (await c.list_resource_templates()).resource_templates
        prompts = (await c.list_prompts()).prompts
    print(f"\\n=== {name} ===")
    for t in tools:
        tag = " [host-only]" if (t.meta or {}).get("audience") == "host" else ""
        tag += " [needs approval]" if (t.meta or {}).get("requires_approval") else ""
        print(f"  tool      {t.name}{tag}")
    for r in resources: print(f"  resource  {r.uri}")
    for t in templates: print(f"  template  {t.uri_template}")
    for p in prompts:   print(f"  prompt    {p.name}({', '.join(a.name for a in p.arguments or [])})")
""")
code("""
def data(result):
    \"\"\"Unwrap an MCP CallToolResult into plain Python data (raises on tool errors).\"\"\"
    if result.is_error:
        raise RuntimeError(result.content[0].text)
    sc = result.structured_content
    return sc["result"] if set(sc) == {"result"} else sc

async def call(server, tool, **args):
    async with Client(SERVERS[server]) as c:
        return data(await c.call_tool(tool, args))

async def read(server, uri):
    async with Client(SERVERS[server]) as c:
        return (await c.read_resource(uri)).contents[0].text
""")

md("""
## 3. Tools that write: create the traveler profile

The original project hard-coded `travel://preferences` (Emirates, window seat, 4-star…).
Now each traveler has a stored profile. We reuse the profile if it already exists, so re-running is safe.
""")
code("""
travelers = json.loads(await read("travel", "travel://travelers"))
existing = next((t for t in travelers if t["name"] == TRAVELER_NAME), None)

if existing:
    TRAVELER_ID = existing["id"]
    print("Using existing profile", TRAVELER_ID)
else:
    created = await call("travel", "create_traveler",
        name=TRAVELER_NAME, home_city=HOME_CITY, home_currency=HOME_CURRENCY,
        preferences={"seat": "aisle", "cabin_class": "economy", "hotel_min_stars": 3, "hotel_max_stars": 4,
                     "preferred_airlines": ["Emirates"], "max_stops": 1, "trip_style": "quiet hotel near the venue"})
    TRAVELER_ID = created["id"]
    print("Created profile", TRAVELER_ID)
""")

md("""
## 4. Resources and resource templates

Resources are **read-only context** the host chooses to load. A *template* takes parameters in its URI.
""")
code("""
profile = json.loads(await read("travel", f"travel://travelers/{TRAVELER_ID}/profile"))
print("Preferences:", json.dumps(profile["preferences"], indent=2))
""")
code("""
# Template with live data: geocoding + country facts + local time
print(await read("travel", f"city://{DESTINATION}/guide"))
""")
code("""
# Generated from data/policy_rules.json — edit that file and this text changes, no code change needed
print(await read("policy", "policy://travel-summary"))
""")

md("## 5. Prompts\n\nA prompt is a reusable, parameterised instruction template served by the MCP server.")
code("""
async with Client(travel_server) as c:
    p = await c.get_prompt("plan_business_trip", {
        "traveler_name": TRAVELER_NAME, "origin": HOME_CITY, "destination": DESTINATION,
        "start_date": START_DATE.isoformat(), "end_date": END_DATE.isoformat(), "purpose": PURPOSE})
PLANNING_PROMPT = p.messages[0].content.text
print(PLANNING_PROMPT)
""")

md("## 6. Real-time tools\n\nEach tool calls a live, keyless public API (Open-Meteo, open.er-api.com, Nager.Date, OpenStreetMap).")
code("""
import pandas as pd

weather = await call("travel", "get_live_weather", city=DESTINATION)
pd.Series(weather).to_frame("now")
""")
code("""
forecast = await call("travel", "get_trip_forecast", city=DESTINATION,
                      start_date=START_DATE.isoformat(), end_date=END_DATE.isoformat())
pd.DataFrame(forecast["days"]) if forecast["available"] else print(forecast["message"])
""")
code("""
flights = await call("travel", "search_flights", origin=HOME_CITY, destination=DESTINATION,
                     departure_date=START_DATE.isoformat(), cabin_class=profile["preferences"]["cabin_class"])
print("Provider:", flights["provider"], "|", flights.get("disclaimer", "live fares"))
pd.DataFrame(flights["offers"])[["airline", "departs_at", "duration_hours", "stops", "total_price", "currency"]]
""")
code("""
p = profile["preferences"]
hotels = await call("travel", "search_hotels", city=DESTINATION, min_stars=p["hotel_min_stars"], max_stars=p["hotel_max_stars"])
print(hotels["price_note"])
pd.DataFrame(hotels["hotels"])[["name", "stars", "distance_to_centre_km", "indicative_nightly_usd"]]
""")
code("""
fx = await call("travel", "convert_currency", amount=1000, from_currency=HOME_CURRENCY, to_currency="INR")
holidays = await call("travel", "get_public_holidays", city=DESTINATION,
                      start_date=START_DATE.isoformat(), end_date=END_DATE.isoformat())
print(f"1000 {HOME_CURRENCY} = {fx['converted']:,} INR (rate {fx['rate']:.4f})")
print("Holidays during trip:", holidays["holidays"] or holidays["coverage_note"])
""")

md("""
## 7. MCP + RAG: policy search and compliance

`search_company_policy` retrieves the most relevant policy sections (OpenAI embeddings, cached on disk; BM25 fallback without a key).
`check_trip_compliance` applies the rules file, converting the hotel rate to the policy currency with a live FX rate.
""")
code("""
hits = await call("policy", "search_company_policy", query="Can I fly business class on a 16 hour flight?", top_k=3)
print("Retrieval mode:", hits["retrieval"])
pd.DataFrame(hits["results"])[["document", "section", "score", "text"]]
""")
code("""
best_flight = min(flights["offers"], key=lambda o: o["total_price"])
rated = [h for h in hotels["hotels"] if h["stars"]]
hotel = rated[0] if rated else hotels["hotels"][0]

compliance = await call("policy", "check_trip_compliance",
    cabin_class=best_flight["cabin_class"], flight_duration_hours=best_flight["duration_hours"] or 0,
    international=True, hotel_stars=hotel["stars"], hotel_nightly_rate=hotel["indicative_nightly_usd"],
    rate_currency="USD", departure_date=START_DATE.isoformat())
print("Compliant:", compliance["compliant"])
for i in compliance["issues"]: print(" -", i)
pd.DataFrame(compliance["checks"])
""")

md("## 8. Work tools: persistent trip tasks")
code("""
task = await call("work", "create_trip_task", title=f"Check visa requirements for {DESTINATION}",
                  traveler_id=TRAVELER_ID, due_date=(START_DATE - timedelta(days=7)).isoformat())
print("Task:", task["id"], "(duplicate)" if task.get("duplicate") else "")
pd.DataFrame(await call("work", "list_trip_tasks", traveler_id=TRAVELER_ID))[["id", "title", "due_date", "status"]]
""")

md("""
## 9. Multiple servers through LangChain's MCPAdapter

The host connects all three servers as one `ClientGroup`. Tool names get a server prefix (`travel_search_flights`).

**Security boundary:** the host reads each tool's MCP `_meta`. Anything marked `audience=host` or `requires_approval`
is never given to the model — the *server* declares it, so there's no hard-coded list of tool names in the host.
""")
code("""
from copilot.host.orchestrator import CopilotHost, TripRequest, is_agent_safe
from copilot.config import Settings

inproc = settings.model_copy(update={"mcp_transport": "inprocess"})
async with CopilotHost(inproc) as host:
    all_tools = await host._adapter.list_tools()
    for t in all_tools:
        print(("agent  " if is_agent_safe(t) else "HOST   ") + t.name)
""")

md("""
## 10. The agent plans the trip

`CopilotHost.plan_trip` loads the MCP resources + prompt into the system prompt, builds a LangChain agent with the
agent-safe tools and streams every step. The agent ends by *requesting* a hotel — it cannot book.
""")
code("""
model = None  # None = use LLM_MODEL from .env
if USE_OFFLINE_DEMO:
    from tests.fake_llm import ScriptedToolModel, trip_script
    model = ScriptedToolModel(script=trip_script(TRAVELER_ID, HOME_CITY, DESTINATION, START_DATE.isoformat(), END_DATE.isoformat()))
elif not (settings.openai_api_key or os.getenv("OPENAI_API_KEY")) and settings.llm_model.startswith("openai"):
    raise SystemExit("Add OPENAI_API_KEY to .env (or set USE_OFFLINE_DEMO = True) to run the agent.")

request = TripRequest(TRAVELER_ID, HOME_CITY, DESTINATION, START_DATE, END_DATE, PURPOSE)
final_plan, PROPOSALS = "", []

async with CopilotHost(inproc, model=model) as host:
    async for ev in host.plan_trip(request):
        if ev.kind == "tool_call":
            print(f"🔧 {ev.title}({', '.join(f'{k}={v}' for k, v in ev.data.items())})")
        elif ev.kind == "tool_result" and ev.data["is_error"]:
            print(f"   ⚠️ {ev.data['result']}")
        elif ev.kind == "approval_request":
            print(f"🟠 awaiting approval: {ev.data['hotel_name']} ({ev.data['id']})")
        elif ev.kind in ("context", "error"):
            print(f"📚 {ev.title}" if ev.kind == "context" else f"❌ {ev.title}: {ev.data}")
        elif ev.kind == "final":
            final_plan = ev.data
    PROPOSALS = list(host.proposals)
""")
code("""
from IPython.display import Markdown
Markdown(final_plan or "_No plan produced — see the log above._")
""")

md("""
## 11. Human approval gate

The request is stored by the Travel server (`travel://booking-requests/pending`), so anyone can approve it — here, in the UI,
or from another session. Only an explicit approval calls the side-effecting `book_hotel` tool.
""")
code("""
async with CopilotHost(inproc, servers=("travel",)) as host:
    pending = await host.pending_requests()
pd.DataFrame(pending)[["id", "traveler_name", "hotel_name", "city", "check_in", "nights", "nightly_rate", "currency"]] if pending else "No pending requests"
""")
code("""
try:
    from IPython.core.error import StdinNotImplementedError
except ImportError:
    StdinNotImplementedError = EOFError

if not PROPOSALS:
    print("Nothing to approve.")
else:
    req = PROPOSALS[0]
    print(f"Approve booking {req['hotel_name']} in {req['city']} for {req['nights']} nights? ")
    try:
        approver = input("Type your name to approve, or press Enter to reject: ").strip()
    except (StdinNotImplementedError, EOFError):
        approver = "Notebook demo approver" if USE_OFFLINE_DEMO else ""
        print("(non-interactive run)", "auto-approving for the demo" if approver else "skipping")
    async with CopilotHost(inproc, servers=("travel",)) as host:
        if approver:
            booking = await host.approve_booking(req["id"], approver)
            print("✅ Booked:", booking["id"], "approved by", booking["approved_by"])
        else:
            await host.reject_booking(req["id"], "notebook user")
            print("❌ Rejected — no side-effecting tool was executed.")
""")

md("""
## 12. STDIO transport

Same servers, but now the host launches each one as a **subprocess** and speaks MCP over stdin/stdout.
This is how desktop clients run local servers.
""")
code("""
import time
stdio = settings.model_copy(update={"mcp_transport": "stdio"})
t0 = time.time()
async with CopilotHost(stdio) as host:
    catalogue = await host.inspect()
    summary = await host.read_text("policy", "policy://travel-summary")
print({name: len(info["tools"]) for name, info in catalogue.items()}, f"tools — connected in {time.time() - t0:.1f}s")
print(summary.splitlines()[1])
""")

md("""
## 13. Streamable HTTP transport

Now the servers run **independently** (as they would in Docker/Kubernetes) and the host connects by URL.
We start them with a bearer token, check `/healthz`, prove unauthenticated calls are refused, then connect properly.
""")
code("""
import subprocess, secrets, urllib.request, urllib.error

API_KEY = secrets.token_urlsafe(16)
ports = {"travel": 8101, "policy": 8102, "work": 8103}
env = {**os.environ, "PYTHONPATH": str(ROOT), "MCP_API_KEY": API_KEY, "PYTHONUTF8": "1"}
procs = [subprocess.Popen([sys.executable, "-m", f"copilot.servers.{n}", "--transport", "http", "--port", str(p)],
                          cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
         for n, p in ports.items()]

def healthy(port):
    for _ in range(60):
        try:
            return urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1).status == 200
        except OSError:
            time.sleep(0.5)
    return False

print({n: healthy(p) for n, p in ports.items()})
try:
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8101/mcp", data=b"{}", method="POST"))
except urllib.error.HTTPError as e:
    print("Without token ->", e.code)
""")
code("""
http = settings.model_copy(update={
    "mcp_transport": "http", "mcp_api_key": API_KEY,
    "travel_mcp_url": "http://127.0.0.1:8101/mcp", "policy_mcp_url": "http://127.0.0.1:8102/mcp", "work_mcp_url": "http://127.0.0.1:8103/mcp"})
try:
    async with CopilotHost(http) as host:
        travelers = await host.read_json("travel", "travel://travelers")
        tasks = await host.call("work", "list_trip_tasks", {"traveler_id": TRAVELER_ID})
    print(f"Over HTTP: {len(travelers)} traveler(s), {len(tasks)} task(s) for {TRAVELER_NAME}")
finally:
    for p in procs:
        p.terminate()
    print("HTTP servers stopped")
""")

md("""
## 14. Use the same servers from any MCP client

The client config is **generated for the machine you run it on** — no hand-edited paths.
`trip-copilot config` (or `python -m copilot config`) does the same from a terminal, and `--install` merges it into the client's config file.
""")
code("""
from copilot.cli import build_config

# This machine's Python, for Claude Desktop
print(json.dumps(build_config("claude-desktop", "local", "trip-", None, None), indent=2))
""")
code("""
# Fully portable: any machine with uv can run the servers straight from a git repo (no local paths at all)
print(json.dumps(build_config("cursor", "uvx", "trip-", "git+https://github.com/<you>/business_trip_copilot_mcp", None), indent=2))
""")

md("""
## Where to go next

* `streamlit run ui/app.py` — the same flow with a UI, approvals inbox and an MCP inspector.
* Set `DUFFEL_ACCESS_TOKEN` for live flight offers.
* Edit `data/policies/*.md` and `data/policy_rules.json` to load your company's policy.
* `docker compose up` — servers over HTTP + UI, ready for a real deployment.
""")

nb = nbf.v4.new_notebook()
nb["cells"] = cells
nb["metadata"] = {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                  "language_info": {"name": "python"}}
out = ROOT / "notebooks" / "business_trip_copilot_walkthrough.ipynb"
out.parent.mkdir(exist_ok=True)
nbf.write(nb, out)
print("wrote", out)
