# Think mode v4 — blueprint-then-fill tailoring

Date: 2026-07-07
Status: approved, implementing

## Why

Think v3 (`core/think_graph.py`) loads the whole wiki bundle and asks one
`draft` call to "tailor this to the target." Two problems surfaced in real use:

1. **Wrong job for the LLM.** Dumping everything and saying "tailor it" puts the
   model on *rewording* (low value). The high-value task — deciding what *this*
   resume should be for *this* role, then finding the evidence — is exactly what
   an LLM is good at, and we weren't asking for it. The output stayed a generic
   "Data Scientist" resume even for a "Data Architect, GenAI" target.
2. **Whole-resume redraft drift.** Every `improve` round re-ran `draft` over the
   entire resume, so untargeted content silently changed (an unrelated 2016 role
   went 3→4 bullets after a summary-only edit). Edits weren't surgical.

New model: **plan, then fill.** The LLM first designs the resume's *shape* from
the target (a blueprint), then selects the candidate's real evidence into that
shape. This mirrors how good humans tailor and how a coding agent works — the
plan generates the "queries," the fill step retrieves the evidence.

This is NOT a scale/retrieval change. The wiki still fits in context and is
still fed whole to `fill`; there is no RAG, no embeddings, no wiki search. The
change is *generation strategy*, not context budget.

## Design decision (locked)

The blueprint is **informed by a light inventory** of the wiki — role titles,
skill categories, project names (no bullets) — so the target shape is both
role-driven AND achievable. It is not blind to the candidate. This keeps themes
from routinely coming up empty and removes fabrication pressure at the source.

## The graph (LangGraph StateGraph)

```
START → understand → blueprint → fill → improve → (done?) ──yes──▶ finalize → END
                                          ▲            │
                                          └─── edit ───┘   (feedback → one change)
                                                       └──quit──▶ END (aborted)
```

`blueprint` and `fill` replace the old single first-pass `draft`. The improve
loop no longer re-runs blueprint/fill — feedback routes to `edit`, which applies
ONE change to the current draft (reusing today's `draft_resume(current=…)`
mechanism). That is what makes edits surgical and kills the drift.

### State (additions to v3)
```
target        str   — JD / intent (+ answers, + web framing)   [unchanged]
inventory     str   — NEW: deterministic light index (roles, skill cats, projects)
evidence      str   — full vetted wiki bundle (loaded once)     [unchanged]
blueprint     dict  — NEW: the target shape (Blueprint schema)
draft         dict  — current resume (Resume schema)            [unchanged]
feedback      str   — "" | "done" | "__abort__" | free text     [unchanged]
rounds        int   — improve-loop safety counter               [unchanged]
out_stem      str                                               [unchanged]
```

### Schemas

New Pydantic model fed to structured output:

```python
class Theme(BaseModel):
    name: str            # e.g. "Agentic AI & LLM orchestration"
    why: str             # one line: why it matters for this target
    priority: int        # 1 = lead with this

class Blueprint(BaseModel):
    title: str                       # target-facing title
    summary_angle: str               # the narrative the summary should take
    themes: list[Theme]              # ordered; what to foreground
    lead_skills: list[str]           # skill clusters to surface first
```

`Resume`/`Job`/`Bullet`/`SkillGroup`/`Education`/`Contact` are reused unchanged.

### Nodes

1. **understand** *(extended)* — pin the target (one simple question if thin),
   optional single web lookup for framing (as v3), load the full bundle into
   `evidence`, and build `inventory` deterministically from the wiki (no LLM):
   role `title — company` lines, the skill category names, and project page
   names. Cheap string assembly from `career/`.

2. **blueprint** *(NEW — one structured call → `Blueprint`)* — input: `target`
   + `inventory`. Designs the resume shape: title, summary angle, ordered
   themes, lead skills. Sees only the inventory, never the full bullets — its
   job is strategy, not copy. System prompt: "You design the SHAPE of a strong
   resume for this target using ONLY themes the candidate can actually support
   (per the inventory). Do not invent themes with no inventory backing."

3. **fill** *(NEW — one structured call → `Resume`, replaces first-pass draft)* —
   input: `blueprint` + full `evidence`. For each theme, SELECT and reword the
   candidate's real bullets/projects that evidence it; order roles/bullets to
   foreground high-priority themes; keep the COMPLETE role history (tailor by
   emphasis, not deletion). **Editor-only: a theme with no supporting evidence
   is dropped, never fabricated.** Same fact-lock rules as v3's DRAFT_SYSTEM
   (metrics/companies/titles/dates locked to the wiki).

4. **improve** *(unchanged)* — show a concise preview (now prefixed with the
   blueprint line, see below) and ask ONE simple question. `done`→finalize,
   `quit`→abort, else→edit.

5. **edit** *(the v3 later-pass path, now its own node)* — `draft_resume` with
   `current` + `feedback`: apply ONE change to the current draft, keep
   everything else. This is what removes drift.

6. **finalize** *(unchanged)* — sanitize (strip em-dash), bundle-aware grounding
   (`validate(generated, store_resume, evidence=bundle)`), render. No KB writes.

### Routers
- `_route_understand`: aborted → END, else → blueprint.
- `_route_improve`: `__abort__` → END; `done` or rounds ≥ MAX_ROUNDS → finalize;
  else → edit. (blueprint/fill only run on the first pass.)

## Transparency: show the blueprint

The first `improve` preview is prefixed with a one-line strategy summary so the
user sees *why* the resume looks the way it does and can redirect strategy, not
just edit output:

```
Tailoring toward: Data Architect, GenAI — leading with Agentic AI orchestration ·
production ML at scale · data architecture.
```

Feedback like "emphasize leadership more" is a normal `edit` on the draft; it
does not re-run the blueprint. (Re-blueprinting is out of scope for v4 — if the
user changes the whole target they can re-run the command.)

## What this fixes / doesn't

Fixes: unfocused generic drafts (blueprint gives intentional structure);
whole-resume redraft drift (edits are surgical). Fabrication guard becomes
structural (drop empty themes) with grounding as backstop.

Does NOT change: the wiki is still read whole (no retrieval), grounding logic,
sanitize, render, fast mode, the `add`/compile flows. The duplicate-project-page
compile bug is tracked separately and untouched here.

## Testing

Extend `tests/test_think_graph.py` (stub-model pattern, no live LLM):
- `blueprint` node: stub returns a `Blueprint`; assert it reaches `fill` and the
  themes are passed into the fill prompt.
- `fill` drops an empty theme: give a blueprint theme with no matching evidence;
  assert no fabricated bullet appears (fill prompt/behavior — verify the theme
  is absent, structurally).
- routers: `_route_improve` feedback → `edit` (not blueprint/fill).
- preview shows the blueprint line.
- existing grounding + schema tests stay green.

## Rollback

v4 is contained to `core/think_graph.py` (+ tests). Reverting the file restores
v3. No data-format or config changes; existing wikis and outputs are unaffected.
