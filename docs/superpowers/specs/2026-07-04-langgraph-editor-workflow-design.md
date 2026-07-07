# Think mode v3 — LangGraph "editor, not author" workflow

Date: 2026-07-04
Status: approved, implementing

## Why

The ReAct `think` agent (v2, `core/think_agent.py`) authored freely and had to
be fenced in with a growing wall of prompt guardrails (attribution, no-fabrication,
no-hedges, suggest-confirm). The workflow was invisible and the model wandered.

New model: **the agent is an EDITOR of vetted material, never an author.** The
wiki already holds the candidate's real, vetted bullets. Tailoring = select +
reorder + reword existing bullets + write a summary. Honesty comes from the
graph *structure* (what each node may do), not from prompt rules. Because the
wiki is small, we feed it whole — so there is **no tool-loop and no wiki search**.

## The graph (LangGraph StateGraph)

```
START → understand → draft → improve → (done?) ──yes──▶ finalize → END
                        ▲                    │
                        └──────── no ────────┘   (loop; safety-capped)
                                             └──quit──▶ END (aborted)
```

### State
```
target        str   — JD / intent (+ any answers the user gave)
evidence      str   — the candidate's FULL vetted wiki bundle (loaded once)
draft         dict  — current resume (structured Resume schema)
feedback      str   — latest user reply ("" | "done" | "__abort__" | free text)
history       list  — user inputs this session (for finalize fact-extraction)
rounds        int   — improve-loop counter (safety cap)
out_stem      str
result        dict  — {aborted, yaml, docx, pdf, ok, problems}
```

### Nodes
1. **understand** — pins the target. If the brief is thin (<~40 chars), ask 1-2
   questions via a blocking prompt. If web is enabled AND a search provider is
   configured, do ONE optional company lookup and fold it into `target` (single
   call, not a loop). Load `evidence = store.read_career_bundle()`.
2. **draft** — one LLM call with structured output (`Resume`): from `target` +
   `evidence` (+ `feedback`/`history` on a loop), SELECT the relevant bullets,
   REORDER, REWORD (better wording, same facts), write a summary. No tools.
3. **improve** — show a concise text preview of the draft + a few targeted
   suggestions (one small LLM call). Read one reply: `done`/`` → finish,
   `quit`/`exit` → abort, else record feedback (+ append to `history`) and loop.
   Safety cap: after N rounds, force finish.
4. **conditional edge** (`_route`): `feedback == "done"` → finalize;
   `"__abort__"` → END; else → draft.
5. **finalize** — if `history` is non-empty, ONE LLM call extracts genuine NEW
   facts the user stated (not editing instructions); the user confirms; confirmed
   facts are written to `raw/` via `store.add_raw` (append-only) so the KB
   compounds. (No auto-recompile — Task-3 staleness will prompt it; the current
   resume keeps the approved facts.) Then run grounding (`agent.validate`),
   write the YAML, and render (`agent.render`).

### Human interaction & LLM usage
- Human check-ins = plain blocking prompts (`ask_fn`/`say_fn`) inside understand,
  improve, finalize. NO LangGraph interrupt/checkpointer (synchronous CLI).
- LLM runs ONLY in: draft, improve-suggest, and finalize-fact-extraction (the
  last only if the user added info). Everything else is deterministic.
- Models via `model.with_structured_output(...)` — `ChatAnthropic` (Anthropic) /
  `ChatOpenAI`→OpenRouter. No `create_agent`, no tool-calling.

## Components
- **new `core/think_graph.py`** — the graph, the `Resume` schema (moved from
  think_agent), `_build_model`, node functions, and `run_think(cfg, *, opening,
  out_stem, ask_fn, say_fn, notify_fn, render_fn, allow_web=True) -> result`.
- **delete `core/think_agent.py`** and `tests/test_think_agent.py`; the
  `list/search/read_wiki` tools are no longer used by think (store helpers stay).
- **`cli._run_think`** calls `think_graph.run_think(...)`.
- **reuses**: `store.read_career_bundle` / `store.add_raw`, `agent.validate`,
  `agent.render`, `paths`. `fast` mode and `render.py` unchanged.
- deps unchanged (`langgraph`, `langchain-anthropic`, `langchain-openai`).

## Grounding & honesty
- Grounding runs on the final draft (facts trace to the store). Editing vetted
  bullets makes fabrication structurally unlikely; the prompt keeps only
  "edit-don't-author / no hedges" wording rules.
- User-approved new facts are legitimate (the user vouched) and are written to
  `raw/`; until a recompile they may show as grounding flags — reported plainly.

## Testing (stub model + real store; no live LLM in unit tests)
- `_route` returns the right next node for done / abort / loop.
- draft post-processing: a stubbed structured model → `Resume` → dict; grounding
  flags a number not in the store.
- finalize write-back: given confirmed facts, a `raw/` entry is created.
- improve loop: `done`/`quit` handled; feedback appends to `history` and loops
  (cap respected).
- Existing render/paths/search/staleness/wiki tests stay green.
- Full graph exercised by a live smoke test (Haiku, isolated home).
