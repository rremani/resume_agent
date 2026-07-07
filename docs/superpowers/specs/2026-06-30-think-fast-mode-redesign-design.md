# Think/Fast mode redesign — two-agent research→format pipeline

Date: 2026-06-30
Status: IMPLEMENTED (core/tailor_chat.py, store wiki-search, cli wiring; live-verified)

## Problem

Generated resumes "look like the user's current resume" and think mode adds
little over fast mode. Root causes:

1. **No real conversation or research.** `think` today just concatenates the
   lines the user types (in `cli._run`) and calls `agent.run_once` once —
   identical to `fast` plus a web flag. No model dialogue, no interview, no
   research, no iterative drafting.
2. **A conservative, generic tailoring prompt.** `agent.SYSTEM` permits free
   rephrasing but gives no push toward recruiter-grade, target-aligned writing.
3. **No retrieval.** `store.read_career_bundle()` concatenates *every* wiki page
   into the prompt. Fine for a small wiki, but it is not the "agent searches for
   the most relevant material" behavior the user wants, and it will not scale as
   the compounding knowledge base grows.

## Target architecture (think mode = two agents)

```
Stage 1: RESEARCH AGENT (strong model = think model, e.g. Opus, web ON)
   interview user + web research + wiki search (LIST/FIND/READ)
   └─▶ emits a DOSSIER ("the best summary having everything")
        ├─ CANDIDATE EVIDENCE  (pulled from the wiki — the ONLY source of facts)
        └─ TARGET RESEARCH     (web — framing/keywords ONLY, never resume facts)
   shown to the user → refine loop on feedback → final dossier

Stage 2: FORMATTER (cheap model = fast model, e.g. Haiku, no web)
   one call: dossier + canonical store ─▶ resume YAML
   └─▶ grounding check (facts must trace to the store)

Deterministic render: YAML ─▶ PDF + DOCX  (existing skill/render.py, untouched)
```

The two configured models are reused meaningfully: **Opus researches → Haiku
formats.** The strong model does the hard reasoning (research, synthesis,
emphasis); the cheap model does mechanical schema-filling.

## Fast mode (unchanged shape)

`fast` stays a **single LLM call** (canonical store + brief → YAML) using the
improved prompt, plus an opt-in `--web` flag. It does NOT use the two-agent
pipeline. Default offline (preserves the Task 2 guarantee); `--web` →
`allow_web=True`.

## Goals

- `think` becomes: **interview → research (web + wiki) → dossier → refine →
  format → render**.
- The research agent **searches its own wiki** (LIST / FIND / READ) instead of
  being handed the whole blob.
- Research uses **Anthropic-native web search** (works with the user's existing
  key; explicit Tavily tool stays optional).
- The dossier is **shown and refinable** by the user.
- A **stronger shared tailoring prompt** raises quality for fast too.

## Non-goals / settled decisions

- **Grounding stays (facts locked).** Resume metrics/companies/titles/dates must
  match the store exactly; grounding runs on the Stage-2 YAML. The agent rewrites
  everything *around* the facts. (User: "bold rewrite, facts locked.")
- **Output contract unchanged.** Both modes emit the same resume YAML schema and
  pass the same grounding check. Mode changes process, never output.
- **No provider rewrite.** Reuse single-shot `Provider.complete()` with a text
  marker protocol (the proven `add`-flow pattern), not native tool-use loops
  (which would break the OpenRouter path).
- **Fast keeps the full-wiki dump and single call.** Agent-driven wiki search is
  think-only. RAG/embedding pre-ranking deferred (YAGNI).
- Interview is **capped (~5 questions)**; a max-turn bound prevents infinite loops.

## The marker protocol (Stage 1 research agent)

Every research-agent turn begins with exactly ONE marker; the driver dispatches
and loops:

| Marker | Meaning | Driver action |
|--------|---------|---------------|
| `ASK: <one question>` | needs user input | show, read answer, append to transcript |
| `LIST` | wants the wiki index | return list of pages (profile, skills, roles, projects) |
| `FIND: <query>` | search the wiki | return ranked page slugs + snippets |
| `READ: <slug>` | read one page | return that page's full text |
| `DOSSIER` + newline + markdown | research done | show dossier, enter refine loop |

Web research happens inside the model's own turn via Anthropic-native web search
(`allow_web=True`) — no marker needed. Loop control words: `done`/`go`
(accept → format → render), `quit`/`exit` (abort).

## Components

### `core/store.py` — wiki retrieval helpers (new)
- `list_pages() -> list[dict]` — index: `{kind, slug, title}` for profile, skills,
  every role (company), every project.
- `search_wiki(query, *, limit=8) -> list[dict]` — keyword-rank pages by term
  overlap (frontmatter + body); return `{kind, slug, title, snippet}`. No
  embeddings/dependency.
- `read_page(slug) -> str | None` — full text of one page by slug.

### `core/tailor_chat.py` — think two-stage orchestrator (new)
- `run_think(provider, *, research_model, format_model, opening, ask_fn, say_fn,
  render_fn, allow_web=True, max_questions=5) -> dict`.
  - Drives the Stage-1 marker loop, shows the dossier, runs the refine loop.
  - On accept, calls Stage 2 (format) then `render_fn`.
  - I/O injected (`ask_fn`/`say_fn`/`render_fn`) for testability, mirroring
    `ingest.add_interactive`.
- `RESEARCH_SYSTEM` — Stage-1 prompt: defines the protocol; instructs the agent
  to LIST first, include the COMPLETE role history (never silently drop a job —
  gaps look bad), use FIND/READ for emphasis/detail, research the target via web,
  ask ≤5 focused questions, then emit a DOSSIER split into CANDIDATE EVIDENCE
  (wiki facts) and TARGET RESEARCH (web, framing only).
- `format_dossier(provider, *, model, dossier) -> str` — Stage-2 call with
  `FORMAT_SYSTEM`: build resume YAML strictly from CANDIDATE EVIDENCE, use TARGET
  RESEARCH only for emphasis/keywords, emit the exact schema, no commentary.
  Reuses `agent.validate` + `compiler.wiki_to_resume_yaml` for grounding and
  `agent.render` for output.

### `core/agent.py` — shared upgrades
- Rewrite `SYSTEM` (used by fast's single call) for recruiter-grade tailoring:
  lead with quantified impact, strong action verbs, mirror target-JD keywords,
  cut weak bullets, sharpen summary — hard fact-lock rules kept verbatim.
- `run_once` already accepts `allow_web`; fast just needs the CLI to pass it.
- Stage-2 formatting reuses `validate`, `wiki_to_resume_yaml`, `render`.

### `cli.py`
- `think`: replace the line-accumulation loop with `tailor_chat.run_think(...)`,
  passing `research_model = cfg.modes.think.model`,
  `format_model = cfg.modes.fast.model`, Rich `ask_fn`/`say_fn`, and
  `render_fn = agent.render`. `allow_web=True`.
- `fast`: add `--web/--no-web` (default off); pass through as `allow_web`.

## Data flow (think)

```
opening/JD ─▶ Stage 1 loop (research_model, web):
   turn ─▶ marker?
      ASK   ─▶ user answer ─┐
      LIST  ─▶ list_pages() ─┤ append to transcript, loop
      FIND  ─▶ search_wiki() ─┤
      READ  ─▶ read_page() ───┘
      DOSSIER ─▶ show to user ─▶ feedback?
                    refine ─▶ (loop Stage 1 with feedback) ─▶ new DOSSIER
                    done   ─▶ Stage 2
Stage 2 (format_model): dossier + store ─▶ YAML
   ─▶ agent.validate(vs wiki_to_resume_yaml) ─▶ grounding flags
   ─▶ agent.render ─▶ PDF + DOCX  ─▶ report
```

## Error handling

- Malformed marker / unparseable DOSSIER or Stage-2 YAML → bounded retries asking
  the model to resend in the correct format; never crashes.
- Web/search failures are best-effort: the turn proceeds without them.
- `FIND`/`READ` for a missing slug → clear "not found" the model can recover from.
- Grounding flags do not block; surfaced in the final report (as today).

## Testing (stub provider, no live LLM)

- Stage-1 protocol: stub scripted `LIST → FIND → READ → ASK → DOSSIER` drives the
  loop; assert each marker calls the right store/IO function and that the dossier
  triggers the refine prompt; `done` runs Stage 2 + `render_fn`.
- Two-model wiring: research turns use `research_model`, the format call uses
  `format_model` (assert via a recording stub).
- Stage-2 grounding: a number absent from the store is flagged on the YAML.
- `search_wiki` ranks an obviously-relevant page above an irrelevant one;
  `read_page` returns content; unknown slug → None.
- `fast --web` sets `allow_web=True`; default `fast` makes zero web calls
  (extends the Task 2 assertion).
- Full suite stays green; no regression to render/paths/search/staleness tests.

## Files

- new: `core/tailor_chat.py`, `tests/test_tailor_chat.py`,
  `tests/test_wiki_search.py`
- changed: `core/store.py` (retrieval helpers), `core/agent.py` (SYSTEM),
  `cli.py` (think wiring, `fast --web`)
- unchanged: `core/providers.py`, `skill/render.py`
