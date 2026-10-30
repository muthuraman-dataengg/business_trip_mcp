# Business Trip Copilot — production MCP project

An enterprise travel copilot built on the **Model Context Protocol**. Three MCP servers (travel, policy, work)
expose real-time tools, resources and prompts; a LangChain agent plans trips through them; a Streamlit UI gives
travelers and approvers a proper front end; and nothing is booked without a human approving it.

```
              Streamlit UI  ·  Jupyter notebook  ·  Claude Desktop / Cursor
                                    │
                         CopilotHost (copilot/host)
           MCP resources + prompt → system prompt │ LangChain agent (create_agent)
                 approval gate (book_hotel only on human action)
                                    │  one ClientGroup, three connections
                                    │  transport: inprocess | stdio | streamable HTTP (+ bearer auth)
        ┌───────────────────────────┼───────────────────────────┐
   Travel MCP                   Policy MCP                   Work MCP
   live weather + forecast      RAG over policy docs         persistent trip tasks
   flights (Duffel / sim)       compliance rules + live FX
   hotels (OpenStreetMap)       policy summary resource
   FX, public holidays
   traveler profiles, booking requests
        └──────────────── SQLite (data/copilot.db) ─────────────┘
```

## What changed from the original

| Original | Now |
|---|---|
| `travel://preferences` hard-coded (Emirates, window seat, 4-star) | Per-traveler profiles in SQLite → resource template `travel://travelers/{id}/profile`, edited in the UI |
| Defaults `Dubai / Bangalore / 5 days`, task owner `"Sunny"`, booking `"4-star"` | Inputs come from the traveler profile and the trip form; the agent chooses the hotel |
| Policy text in Python, ₹12,000 duplicated in two places | `data/policies/*.md` (RAG) + `data/policy_rules.json` (rules); the summary resource is generated from the rules |
| Embeddings recomputed on every server start | Cached on disk by content hash; BM25 fallback when no API key |
| Fixed flight list, fake booking ID | Duffel live offers (or a clearly labelled simulator), real hotels from OpenStreetMap, real bookings table |
| Blocking `urllib`, no timeouts/retries | Async `httpx` with retries, timeouts, per-loop pooling and TTL caching |
| Agent exclusion by hard-coded name `travel_book_hotel` | Servers tag tools with MCP `_meta` (`audience=host`, `requires_approval`); the host filters on that |
| Approval via `input()` in the terminal | Persistent booking requests; approve/reject from the UI (any session), notebook or code |
| Separate `app.py`/`app_http.py` and wrapper files | One host, transport chosen by `MCP_TRANSPORT` |
| 50+ packages (torch, transformers…) | Lean `requirements.txt` |
| — | `/healthz`, bearer-token auth, DNS-rebinding protection, Docker Compose, 11 tests |

## Real-time use cases

* **Plan a trip end to end.** Weather now and for the trip dates, holidays at the destination, outbound and return
  flights in the preferred cabin, hotels in the allowed star band, policy check, prep tasks, one hotel sent for approval.
* **Policy questions.** "Can I fly business to Singapore?" uses semantic search over the policy documents with citations.
* **Compliance before booking.** Cabin entitlement by flight length, hotel star cap, nightly budget converted at today's
  FX rate, booking-window check, required approvers.
* **Approver inbox.** Managers see every pending request, adjust nights or check-in, and approve or reject.
* **Trip prep tracking.** De-duplicated tasks with due dates and status.
* **Use from any MCP client.** The same servers plug into Claude Desktop, Cursor or VS Code.

All external APIs are free and keyless (Open-Meteo, open.er-api.com, Nager.Date, REST Countries, OpenStreetMap
Overpass). Flights use Duffel when `DUFFEL_ACCESS_TOKEN` is set; otherwise a deterministic simulator based on the real
distance, with every result marked `"source": "simulated"`. Hotel prices are indicative (OSM has no prices) and are
labelled that way.

## Setup

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
python -m pip install -U pip
pip install -e ".[ui,dev]"             # installs the package + the `trip-copilot` command
copy .env.example .env                 # then put your OPENAI_API_KEY in .env
trip-copilot info                      # shows where data, database and .env are
```

Works on Python 3.10 – 3.13, on Windows, macOS and Linux. Any LangChain chat model works: set `LLM_MODEL`
(for example `anthropic:claude-sonnet-5-5`) and install its package. `requirements.txt` is still there if you
prefer `pip install -r requirements.txt`.

**Where things live** (no paths are hard-coded):

| Running from | Data folder (database, editable policies) | `.env` read from |
|---|---|---|
| This repo / `pip install -e .` | `./data` | `./data/.env`, repo `.env`, current folder `.env` |
| `pip install` / `uvx` elsewhere | Windows `%LOCALAPPDATA%\trip-copilot` · macOS `~/Library/Application Support/trip-copilot` · Linux `~/.local/share/trip-copilot` | data folder `.env`, current folder `.env` |
| Anywhere | `DATA_DIR` environment variable overrides both | |

Default policies ship inside the package (`copilot/seed/`) and are copied into the data folder on first run.
Edit the copies; they're never overwritten.

## Run it

**UI** (fastest locally: choose *inprocess* in the sidebar, or set `MCP_TRANSPORT=inprocess`)

```powershell
streamlit run ui/app.py
```

1. Create a traveler profile with their preferences (first launch).
2. **Plan a trip**: enter destination and dates; watch each MCP tool call stream in; read the plan, forecast chart,
   flight and hotel tables, and policy result.
3. **Approvals**: enter your name, adjust if needed, approve or reject. Only now is `book_hotel` called.
4. **Tasks**, **Traveler profile** and **MCP inspector** (browse tools, resources and prompts, and call any tool).

**Notebook:** `notebooks/business_trip_copilot_walkthrough.ipynb` covers one MCP concept per step
(servers, tools, resources, templates, prompts, RAG, multi-server adapter, agent, approval, STDIO, HTTP, client config).
Register the venv as a kernel first: `python -m ipykernel install --user --name trip-copilot`.
No key or internet? Set `COPILOT_OFFLINE_DEMO=1` before launching Jupyter.

**HTTP servers** (separate processes, as in production)

```powershell
python scripts/start_servers.py     # travel :8001, policy :8002, work :8003, with /healthz
# then set MCP_TRANSPORT=http for the UI / notebook
```

Set `MCP_API_KEY` to require `Authorization: Bearer <key>`, and `MCP_ALLOWED_HOSTS` to restrict Host headers.

**Docker**

```bash
docker compose up --build           # UI on http://localhost:8501
```

**Claude Desktop, Cursor, VS Code, Claude Code.** Generate the config on the machine that will run it:

```powershell
trip-copilot config                                   # print Claude Desktop config for this machine
trip-copilot config --client claude-desktop --install # merge into Claude Desktop's config (backup kept)
trip-copilot config --client cursor --install         # ~/.cursor/mcp.json
trip-copilot config --client vscode --install         # .vscode/mcp.json in the current folder
trip-copilot config --client claude-code --install    # .mcp.json in the current folder
```

Three modes, from most local to most portable:

| `--mode` | Generated entry | Use when |
|---|---|---|
| `local` (default) | `"command": "<this venv's python>", "args": ["-m", "copilot.servers.travel"]` | Running on this machine |
| `uvx --source git+https://github.com/<you>/business_trip_copilot_mcp` | `"command": "uvx", "args": ["--from", "<git url>", "trip-travel-mcp"]` | Any machine with `uv`, no clone, no paths |
| `http --base-url https://mcp.example.com` | `"url": "https://mcp.example.com/travel/mcp"` (+ bearer header if `MCP_API_KEY` is set) | Servers deployed centrally (Docker) |

Before `pip install -e .`, use `python -m copilot config` instead of `trip-copilot config`. The first `uvx` launch
downloads dependencies and may outlast a client's start-up timeout; run
`uvx --from <source> trip-travel-mcp --help` once to warm the cache.

**Tests**

```powershell
pytest -q        # offline: external APIs and the LLM are faked
```

## Project layout

```
copilot/
  config.py            settings from .env; data folder + .env discovery (no hard-coded paths)
  cli.py               `trip-copilot` command: config | serve | info
  seed/                default policies shipped with the package (copied to the data folder)
  models.py            traveler, preferences, task, booking, booking request
  db.py                SQLite (WAL) repository; swap for Postgres with the same functions
  policy_index.py      policy loader, section chunking, cached embeddings + BM25
  integrations/        geo, weather, finance (FX), holidays, flights, hotels (async httpx)
  servers/             travel.py, policy.py, work.py, runner.py (stdio | http + auth + healthz)
  host/                connections.py (transports), orchestrator.py (context, agent, approval gate)
ui/app.py              Streamlit app
notebooks/             step-by-step walkthrough
data/                  runtime folder for a checkout: policies, rules, database, cache (created on first run)
pyproject.toml         package metadata and console commands
scripts/               start_servers.py, build_notebook.py
tests/                 offline fakes, scripted LLM, server and host tests
legacy/                the original project, kept for reference
```

## Customising

* **Your company policy:** replace `policies/*.md` in your data folder (`trip-copilot info` shows where) (each `##` section is one retrievable chunk) and the
  thresholds in `policy_rules.json` there. No code changes needed. To change the defaults for everyone, edit `copilot/seed/`.
* **Real task system:** replace the `db.*_task` calls in `copilot/servers/work.py` with Jira, Linear or ServiceNow.
  The MCP tool contract stays the same.
* **Real booking:** implement the provider call inside `book_hotel` (travel server). It only ever runs after approval.
* **Postgres:** reimplement `copilot/db.py` with the same function signatures.

## Production notes

* Stateless JSON HTTP servers scale horizontally behind a load balancer. Put TLS at the ingress, and use OAuth
  (`MCPServer(auth=...)`) instead of the shared bearer key for multi-tenant deployments.
* SQLite in WAL mode is fine for a team; use Postgres for more concurrent writers.
* `langchain.mcp` is marked beta by LangChain; versions are bounded in `requirements.txt`.
