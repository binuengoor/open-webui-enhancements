# Open WebUI Setup

This folder contains the three files you actually need for Open WebUI:

- `enhanced_websearch.py` — import as a Tool
- `skill.md` — import as a Skill
- `prompt.md` — paste into the model System Prompt

## Recommended setup

Use these together for a Perplexity-style general-purpose model:

1. import the tool wrapper
2. import the skill
3. create or edit your model preset
4. paste in the system prompt
5. attach the skill to the model
6. enable Native / Agentic tool calling

---

## 1) Import the tool wrapper

In Open WebUI:

- go to `Workspace -> Tools`
- create a new tool or import from file
- upload `enhanced_websearch.py`

This adds these tools:

- `concise_search`
- `research_search`
- `fetch_page`
- `extract_page_structure`

### Tool environment / valves

Set these if needed:

- `EWS_SERVICE_BASE_URL`
- `EWS_BEARER_TOKEN`
- `EWS_REQUEST_TIMEOUT`

Suggested values:

```text
EWS_SERVICE_BASE_URL=http://localhost:8091
EWS_BEARER_TOKEN=
EWS_REQUEST_TIMEOUT=120
```

Adjust the base URL if Open WebUI reaches the service through another host.

---

## 2) Import the skill

In Open WebUI:

- go to `Workspace -> Skills`
- create a new skill or import from markdown
- upload `skill.md`

This skill reinforces how the model should use:

- direct answering
- `concise_search`
- `research_search`
- verification tools
- sequential reasoning
- parallel search-plus-research workflow

---

## 3) Configure the model preset

In Open WebUI:

- go to `Workspace -> Models`
- create a new model preset or edit an existing one
- pick your base model
- attach the imported tool(s)
- attach the imported skill
- paste `prompt.md` into the System Prompt field

Recommended model goal:

- general-purpose Perplexity replacement
- concise for simple questions
- deeper and source-backed for research questions

---

## 4) Recommended settings

Use Native / Agentic Mode if your model supports good tool calling.

Recommended:

- Function Calling: `Native`
- Enable the imported Enhanced Websearch tool
- Attach the imported skill
- Keep the prompt from `prompt.md`
- Also allow supporting tools the model can call intelligently when useful

Helpful supporting tools in your setup include:

- `sequential-thinking` for explicit multi-step planning on hard questions
- `time` for current date/time-sensitive answers
- `fetch` or `playwright` when a page needs direct inspection outside the wrapper path
- `Sub Agent` for parallel work on genuinely multi-track tasks
- `Weather`, `News Reader`, `Youtube Tool`, or `Reddit Explorer` when the question is clearly domain-specific
- `obsidian-mcp`, `vikunja`, `linkwarden`, or `sparkyfitness` only when the request is about the user's own systems/data and those tools are actually relevant

Do not bloat the prompt by enumerating every possible tool behavior. The system prompt should establish the primary search/research workflow; the model can opportunistically use other tools when they clearly improve answer quality.

If the model is weak at tool calling, this setup will be less reliable. It works best with stronger reasoning models.

---

## 5) How the model should behave

Expected behavior:

- answer directly when retrieval is unnecessary
- use `concise_search` for quick current information
- use `research_search` for broader or deeper questions
- verify specific URLs with `fetch_page` when needed
- use a parallel search-plus-research pattern for higher-value research tasks

That means:

- the model does not always need research
- the model should stop once answer quality is good enough
- the model should synthesize instead of dumping raw JSON

---

## 6) Practical notes

- `research_search` is slower and deeper; default depth should usually be `balanced`
- `speed` is for quick passes
- `quality` is for slower, heavier work only when justified
- `concise_search` should usually be the first retrieval step
- if the backend requires auth, make sure the bearer token is configured in the tool settings

---

## Files in this folder

- `enhanced_websearch.py` — Open WebUI tool wrapper
- `skill.md` — Open WebUI skill
- `prompt.md` — system prompt for the model preset
- `README.md` — this setup guide
