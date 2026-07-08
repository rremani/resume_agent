# Resume Agent — Engineering Handoff

This document hands off four work items on an existing, working Python codebase.
Read the project `README.md` first for architecture. Decisions below are settled
(don't relitigate): **stay in Python**, and **remove the LibreOffice dependency**.

## Context: what exists today

A terminal agent that builds a compounding career knowledge base from a resume
and generates tailored resumes. Key design facts the tasks depend on:

- **Data model (Karpathy LLM-wiki pattern):** `raw/` is immutable, append-only
  source of truth; `career/` is a compiled wiki (markdown + YAML frontmatter)
  the LLM maintains. Every metric/company/date in `career/` must trace back to
  `raw/` — grounding runs on the *compile* step. Do not break this invariant.
- **Providers:** `core/providers.py` is one interface over Anthropic + OpenRouter.
  New external integrations should follow this same swappable-interface pattern.
- **Modes:** `fast` (cheap model, no web, single-shot) and `think` (stronger
  model, web, conversational). Both emit the SAME resume YAML schema and pass the
  SAME grounding check. Preserve this: mode changes process, never output contract.
- **Render skill:** `skill/render.py` is deterministic: resume YAML → DOCX → PDF.
  It is intentionally self-contained (a "skill"). Keep it that way.

File map: `core/store.py` (raw+career IO), `core/extract.py` (markitdown→md),
`core/compiler.py` (raw→wiki + grounding), `core/ingest.py` (bootstrap + add),
`core/agent.py` (tailoring), `core/providers.py`, `core/config.py` (onboarding,
.env, config.yaml), `skill/render.py`, `cli.py` (Typer+Rich).

---

## TASK 1 — Replace LibreOffice PDF rendering (HIGHEST PRIORITY)

**Why:** `skill/render.py` currently shells out to `soffice` (LibreOffice) to
convert DOCX→PDF (see the `subprocess.run([...soffice...])` call near the end of
`build()`). This is a hard external dependency that blocks clean distribution
(PyInstaller/pipx) and must be removed entirely. This is the prerequisite for
Task 4.

**What to do:** Generate the PDF directly in Python — do NOT convert from DOCX.
Keep DOCX generation (python-docx) as-is for the `.docx` output; add an
independent PDF path so the two outputs are produced from the same resume-YAML
model by two renderers.

**Recommended approach:** Build the PDF with **ReportLab** (Platypus) or **fpdf2**
— both are pure-Python, pip-installable, no system binaries. ReportLab is more
capable for the FAANG-style layout (flowables, paragraph styles, tables for the
header/skills); fpdf2 is lighter. Match the existing visual style: monochrome,
single-column, serif (Georgia/Times), right-aligned dates, section rules,
experience-first ordering. Reference the current style constants in `render.py`
(`FONT`, `BLACK`, `DARK`, margins, sizes).

**Acceptance:**
- `skill/render.py` produces `<stem>.pdf` and `<stem>.docx` with **zero**
  subprocess calls and no `soffice` reference anywhere in the repo.
- Output is 1–2 pages, visually close to the current design.
- Works on a machine with no LibreOffice installed.

---

## TASK 2 — Pluggable web search tool (Tavily + swappable)

**Why:** Current "web search" is only the provider's built-in (Anthropic native /
OpenRouter `:online`) — opaque and not tunable. Add an explicit, inspectable
search tool the `think`-mode agent can call: research a target company before
tailoring, pull a JD from a URL, verify exact certificate names during `add`.

**What to do:** Create `core/search.py` with a small `SearchTool` interface
mirroring the `Provider` pattern, plus a `TavilyProvider` implementation. Make it
swappable (Tavily / Exa / Brave) via config — do not hardcode Tavily. Key read
from env (`TAVILY_API_KEY`) and added to the onboarding `.env` flow in
`core/config.py`. Expose results to the agent in `think` mode only (respect the
existing `allow_web` flag on the mode). `fast` mode stays offline.

**Acceptance:**
- `core/search.py` with an interface + at least Tavily implemented; provider
  selected via `config.yaml`, key via `.env`.
- `think` can use it; `fast` cannot.
- Onboarding optionally captures the search key into `.env` (git-ignored).
- No web calls in `fast` mode (assert/test this).

---

## TASK 3 — Detect manual edits to `raw/` and re-sync the wiki

**Why:** If a user hand-edits a file in `raw/`, nothing notices, and the compiled
`career/` wiki silently goes stale. `recompile` exists but is manual and rebuilds
everything. Add change detection so the wiki can't drift from its source unknowingly.

**What to do:**
- On compile, record a manifest (e.g. `career/.manifest.json`) of each `raw/`
  file's hash (sha256) + mtime, representing "what the wiki was last built from."
- Add `core/store.py` helpers: `raw_fingerprint()` and `wiki_is_stale()` (compares
  current raw hashes to the manifest).
- On every command that reads the wiki (`fast`, `think`), if stale, warn clearly:
  "raw/ changed since last compile — run `resume recompile`" (or auto-recompile
  behind a `--auto` flag; default to warn, don't surprise the user with LLM calls).
- **Stretch (optional):** incremental compile — only regenerate wiki pages whose
  source raw files changed, instead of the whole history. Reduces cost and drift.
  Only attempt if it doesn't complicate the grounding step.

**Acceptance:**
- Editing a `raw/` file and running `fast`/`think` produces a staleness warning.
- `recompile` updates the manifest so the warning clears.
- Grounding invariant (career facts trace to raw) still holds after re-sync.

---

## TASK 4 — Single-command distribution (no per-machine Python pain)

**Why:** The user's main complaint: installing on every system is painful. Goal is
a `claude`/`codex`-like experience — install once, type `resume`, it runs.
**Depends on Task 1** (LibreOffice must be gone first; a bundled binary can't ship
LibreOffice cleanly).

**What to do (two tiers, ship tier A first):**
- **Tier A — pipx:** ensure `pyproject.toml` exposes the `resume` console script
  (it already declares `[project.scripts] resume = "cli:app"` — verify it works
  end-to-end) so `pipx install resume-agent` gives a global `resume` command in an
  isolated env. Document it. This is low-effort and removes most pain.
- **Tier B — frozen binary:** add a PyInstaller (or Nuitka) build producing a
  single self-contained executable per OS (macOS/Linux/Windows), so users with no
  Python can download-and-run. Expect large binaries and a per-OS build matrix
  (e.g. GitHub Actions). Confirm markitdown and python-docx bundle correctly.

**Acceptance:**
- Tier A: clean `pipx install` yields a working global `resume` command.
- Tier B: a frozen binary runs `resume bootstrap`/`fast`/`think` on a machine with
  no Python and no LibreOffice installed.

---

## Cross-cutting notes

- **Don't break the grounding invariant** in any task. It's the core safety
  property (prevents resume facts drifting, e.g. 90%→92%).
- **Keep fast/think on one shared YAML output contract.**
- **Secrets:** all new keys (Tavily etc.) go to git-ignored `.env`, never
  `config.yaml`. Follow the existing pattern in `core/config.py`.
- **Sequencing:** Task 1 → Task 4 (1 unblocks 4). Tasks 2 and 3 are independent
  and can proceed in parallel.
- **Testing reality:** the original author validated non-LLM paths with stub
  providers because the build sandbox had no API key. Recommend the agent add
  stub-provider tests for the new code paths the same way, plus at least one live
  smoke test with a real key before release.
