# Open WebUI Wrapper Diagnosis

This artifact satisfies the MP-00 pre-work requirement to explain why the Open WebUI wrapper currently feels weaker than MCP.

## Current wrapper behavior

The Open WebUI tool in `enhanced-websearch/enhanced_websearch.py` is a thin async shell around blocking stdlib HTTP calls.

What it does today:

- exposes four tool methods: `concise_search`, `research_search`, `fetch_page`, `extract_page_structure`
- maps them to backend endpoints:
  - `POST /search`
  - `POST /research`
  - `POST /fetch`
  - `POST /extract`
- forwards only a small subset of arguments plus an optional bearer token
- emits local Open WebUI status events every 10 seconds while waiting
- returns the backend response as a JSON string

Operationally, the wrapper does not orchestrate research itself. It waits for one backend HTTP response and periodically tells Open WebUI that work is still in progress.

## Where it diverges from backend and MCP behavior

### 1. MCP has a broader and more explicit contract surface

`app/mcp_server.py` exposes:

- `search`
- `research`
- `fetch_page`
- `extract_page_structure`
- `health_check`
- `providers_health`

The wrapper only exposes the first four. That means MCP clients can inspect health and provider cooldown state directly, while Open WebUI cannot. When the system is flaky, MCP has first-class debugging affordances that the wrapper lacks.

### 2. MCP preserves more backend search semantics

The MCP `search` tool does small but important contract-normalization work before calling `/search`:

- validates `search_recency_amount >= 1`
- converts multi-unit recency windows into `search_after_date_filter`
- disables the fixed one-unit recency bucket when needed
- tags requests with `client: "mcp"`

The wrapper forwards `search_recency_amount` directly to `/search`, but `/search`'s request model does not define that field. Because `PerplexitySearchRequest` is `extra="ignore"`, the backend silently drops it.

Result:

- wrapper users can ask for `3 months`
- backend only sees `month`
- effective behavior collapses to a one-month bucket
- MCP does not have this problem because it translates the request into canonical backend fields first

This is the clearest concrete parity bug found in the current code.

### 3. The wrapper returns serialized JSON text, not a structured object

Each wrapper method ends with `return json.dumps(response, ensure_ascii=False)`.

MCP returns native structured JSON objects. That matters because:

- MCP clients can reliably inspect fields
- Open WebUI gets a text blob and must reinterpret it
- failures and partial-success cases are less naturally typed in the wrapper path

Even if Open WebUI can work with JSON strings, it is a weaker interface boundary than MCP's typed tool return.

### 4. Wrapper progress is local and synthetic, not backend-driven

The wrapper emits status messages like:

- "Concise search still working..."
- "Long-form research still working..."

These are timers in the wrapper, not real backend stage updates. MCP avoids pretending to stream progress; it simply behaves synchronously.

Result:

- wrapper progress does not reflect planning, retrieval, followup, Vane, synthesis, or fallback stages
- long calls can still feel hung or opaque
- the wrapper cannot distinguish slow-but-healthy from slow-and-stuck

This matches the master-plan concern that the wrapper should consume real backend progress once SSE exists, rather than inventing its own pseudo-progress.

### 5. Timeout defaults are mismatched with backend expectations

The wrapper README says default `EWS_REQUEST_TIMEOUT` is `25`, but the actual wrapper code defaults to `60` seconds. MCP defaults to `25` via `EWS_MCP_REQUEST_TIMEOUT` falling back to `EWS_REQUEST_TIMEOUT`.

This inconsistency is not the main quality problem, but it increases confusion when comparing wrapper and MCP behavior and can make one client appear more or less reliable depending on the query.

### 6. Open WebUI is one layer further from the canonical contract

MCP lives in the same backend codebase and is mounted on the same ASGI app at `/mcp`. It shares the same deployment boundary, auth model, and contract assumptions.

The wrapper is imported into a separate Open WebUI tool runtime. That adds another failure plane:

- Open WebUI tool runtime constraints
- blocking stdlib HTTP inside a separate environment
- its own per-user valves and defaults
- stringification/interop behavior at the tool boundary

So even when the wrapper is intentionally thin, it is still more exposed to runtime mismatch than MCP.

## Likely reasons the wrapper is weaker or flakier

### Contract drift

The biggest diagnosed issue is contract drift, especially around concise search knobs. MCP translates user-friendly inputs into the actual backend `/search` contract, while the wrapper currently forwards at least one unsupported field (`search_recency_amount`) and relies on the backend to ignore unknowns.

### Poor observability on the wrapper path

The wrapper has no `health_check` or `providers_health` tools and no backend-native progress stream. When behavior is degraded, users only see a delayed final payload or a generic error string.

### String-based tool output

Returning serialized JSON instead of a structured object makes the wrapper path more brittle and less inspectable than MCP.

### Extra runtime boundary

MCP is co-resident with the backend app; the wrapper is not. That means more chances for timeout, networking, auth, and tool-runtime mismatch.

### Fake progress instead of real stage visibility

The wrapper's periodic status updates improve UX slightly, but they do not make the system more correct. They can mask the absence of true backend streaming and make debugging harder because the visible state is not tied to actual orchestrator phases.

## What the wrapper should own

The wrapper should own only Open WebUI-specific adaptation:

- exposing tool names and argument schemas that fit Open WebUI
- forwarding auth and base URL configuration
- minimal request normalization only when needed to preserve backend contract parity
- rendering backend progress events into Open WebUI status updates once streaming exists
- returning backend payloads with as little transformation as possible

## What the wrapper should not own

The wrapper should not own research semantics or orchestration logic:

- no provider routing logic
- no search/research mode decision logic beyond thin contract mapping
- no fallback policy
- no synthesis or citation assembly
- no health inference from timer-based status loops
- no duplicated query-planning behavior already present in backend or MCP

## Recommended MP-00 conclusion

The wrapper is weaker than MCP primarily because MCP already performs the small amount of contract adaptation that the backend actually needs, while the Open WebUI wrapper mostly forwards requests blindly and returns stringified JSON. The biggest concrete bug is that wrapper `search_recency_amount` does not survive the `/search` contract, so wrapper behavior diverges from MCP for multi-unit recency windows.

Therefore:

- backend remains the canonical owner of semantics
- MCP is currently the most faithful thin client
- the Open WebUI wrapper should be repaired for contract parity and backend-driven progress, not expanded into another orchestration layer
