# Resume Agent

Terminal agent that builds a **compounding career knowledge base** from your
existing resume, lets you grow it conversationally over time, and generates
tailored, FAANG-style resumes on demand.

Data model follows Andrej Karpathy's **LLM-wiki** pattern, adapted with
provenance discipline so resume facts can never drift.

## Data model

```
raw/        immutable, append-only source of truth (the grounding anchor)
              <date>-resume-original.md     your first resume, converted once
              <date>-project-<slug>.md      each later addition (never edited)
              <date>-cert-<slug>.md
career/     compiled wiki the agent maintains (markdown + YAML frontmatter)
              profile.md                    name, contact, summary, education
              skills.md                     compiled skill clusters
              roles/<company>.md            one page per role
              projects/<slug>.md            one page per notable project
```

Two integrity rules:
1. **raw/ is append-only** — facts are never rewritten, only added.
2. **Every metric/company/date in career/ must trace to raw/** — grounding runs
   on the *compile* step, so synthesis can't drift your numbers (the known
   "knowledge drift" failure mode of LLM wikis). A resume can't afford a 90%→92%
   slip; this prevents it.

The resume generator reads the compiled wiki, not raw — so it benefits from clean,
cross-linked, deduplicated knowledge while the originals stay pristine.

## Install

Install once, then type `resume` from anywhere — like `claude` or `codex`.

```bash
# Tier A — isolated global command (recommended)
pipx install .            # or: uv tool install .
resume onboard

# Or run from source without installing:
pip install -r requirements.txt
python cli.py onboard
```

A self-contained binary (no Python required) can also be built — see
[Distribution](#distribution).

## Lifecycle

```bash
resume onboard
#   → choose provider (anthropic / openrouter)
#   → pick fast / think models (sensible defaults offered)
#   → paste your API key  → saved to a git-ignored .env (NOT config.yaml)
#   → optionally point it at your existing resume → wiki built on the spot

# If you skipped the resume during onboarding, build it any time:
resume bootstrap my_resume.pdf

# Over time — add new experience conversationally
resume add "finished a consulting project on fraud detection"
#    → the agent asks free-form follow-ups (client? scale? impact? tech? when?)
#    → writes ONE immutable raw entry, then recompiles the affected wiki pages
resume add "got AWS Solutions Architect certification"

# Any time — generate a tailored resume
resume fast  "GenAI role at a bank, emphasize LLM + risk"
resume think --file jd.txt
```

Other commands: `status` (show config), `recompile` (rebuild wiki from raw).

## Data location

All your data — the immutable `raw/` sources, the compiled `career/` wiki,
generated `output/`, plus `config.yaml` and the secret `.env` — lives under a
single per-user home so an installed/frozen build never writes inside its own
package:

```
~/.resume-agent/        default home (override with $RESUME_AGENT_HOME)
```

Set `RESUME_AGENT_HOME=/path/to/dir` to keep multiple stores or point at an
existing one.

### Staying in sync

`raw/` is the source of truth and the `career/` wiki is compiled from it. Each
compile records a manifest (`career/.manifest.json`) of every raw file's
sha256. If you hand-edit, add, or remove a `raw/` file, `fast`/`think` detect
the drift and warn:

```
⚠ raw/ changed since last compile (1 modified) — the career wiki may be out of date.
  run resume recompile to re-sync (or pass --auto to recompile automatically).
```

`resume recompile` rebuilds the wiki and refreshes the manifest, clearing the
warning. Pass `--auto` to `fast`/`think` to recompile on the spot instead of
warning. Content (sha256), not mtime, decides staleness — re-saving a file with
no changes won't trigger it.

## Secrets

Your API key is captured during onboarding and written to a **git-ignored `.env`**,
never to `config.yaml`. The file is chmod `600` (owner-only) and loaded
automatically on every run, so you set the key once. `.gitignore` keeps `.env`,
`config.yaml`, generated outputs, and your personal `raw/`+`career/` data out of
version control and out of any zip you share.

## Modes

| Mode  | Default model (anthropic) | Web     | Shape                                    |
|-------|---------------------------|---------|------------------------------------------|
| fast  | `claude-haiku-4-5`        | `--web` | one shot: store + brief → YAML           |
| think | `claude-sonnet-4-6`       | on      | LangGraph editor loop: draft → refine    |

Both emit the same YAML schema and pass the same grounding check. (OpenRouter
defaults: fast `google/gemma-4-31b-it`, think `google/gemini-3.5-flash`.)

**fast** is a single LLM call — instant and offline by default; pass `--web` to
let it research when useful.

**think is a LangGraph "blueprint, then fill" workflow** (`core/think_graph.py`):

```
START → understand → blueprint → fill → improve → (done?) ─yes→ finalize → END
                                          ▲          │
                                          └── edit ──┘   (feedback → one change)
```

1. **understand** — pin the target (one simple question if the brief is thin);
   optionally run one web lookup for framing keywords; load your **complete
   verified wiki** as evidence plus a light **inventory** (role titles, skill
   categories, project names — no bullets).
2. **blueprint** — design the *shape* of a strong resume for this target from
   the inventory: ordered themes, summary angle, lead skills. Strategy, not copy
   — and only themes your inventory can actually support.
3. **fill** — select and reword your **real** bullets/projects into that
   blueprint. A theme with no supporting evidence is *dropped, never fabricated*.
4. **improve** — shows a concise preview (prefixed with the blueprint strategy,
   so you can redirect the *plan*, not just the text) and asks one simple
   question. You drive the changes and say `done`.
5. **edit** — applies your one change surgically to the current draft (no
   whole-resume redraft, so untouched roles don't drift).
6. **finalize** — ground-check, sanitize, render. **No knowledge-base writes.**

Picking what to foreground is where an LLM shines, so `think` spends its
reasoning on *selection and structure* (blueprint → fill) rather than rewording
everything. The wiki still fits in context and is fed whole — no retrieval/RAG.

It is an **editor of your vetted material — never an author.** It never invents,
never solicits new projects, and never touches the wiki; capturing new
experience is the separate `resume add` flow. Facts come only from your wiki;
web research informs framing, never numbers. Grounding runs before render, so a
90%→92% drift (or a truncated company name) is caught first.

## Tailoring

The tailoring agent writes **recruiter-grade** bullets — impact-first, strong
action verbs, aligned to the target brief's keywords — while grounding locks
every metric, company, title, and date to your store. It rewrites freely
*around* the facts but can't change or invent them ("bold rewrite, facts
locked"), and keeps your complete role history, tailoring by emphasis rather
than deletion. So the resume reads sharp without drifting your numbers.

## Providers

`core/providers.py` is one interface over **Anthropic** (native web search) and
**OpenRouter** (`:online` suffix). Switch via `resume onboard`.

## Architecture (files)

```
core/store.py      raw + career read/write, frontmatter parsing, wiki search, staleness
core/paths.py      per-user data home resolution (~/.resume-agent)
core/extract.py    document → markdown (MarkItDown primary, pypdf fallback)
core/compiler.py   raw → career wiki (LLM), grounded against raw; wiki → resume YAML
core/ingest.py     bootstrap (doc→raw→wiki) and conversational add
core/agent.py      fast tailoring + grounding validator + render entry point
core/think_graph.py think mode: LangGraph editor workflow (understand→draft→improve→finalize)
core/providers.py  Anthropic + OpenRouter behind one interface
core/search.py     pluggable web-search tool (Tavily / Exa / Brave) for think mode
core/config.py     onboarding + config.yaml + per-mode model defaults
skill/render.py    deterministic skill: YAML → fixed-style PDF + DOCX (pure Python)
cli.py             Typer + Rich terminal surface
```

`think` is built on **LangGraph** (`langgraph` + `langchain-anthropic` /
`langchain-openai`); the resume schema is a Pydantic model fed to the model's
structured-output mode, so drafts are validated objects, not parsed text.

## Web search (think mode)

Beyond the model provider's built-in web search, `think` mode can use an
**explicit, inspectable search tool** to research a target company, pull a JD,
or verify a fact before tailoring. It mirrors the provider pattern: one
interface, swappable backends, selected via `config.yaml`:

```yaml
search:
  provider: tavily        # tavily | exa | brave | none
  max_results: 3
```

The API key (`TAVILY_API_KEY` / `EXA_API_KEY` / `BRAVE_API_KEY`) lives in the
git-ignored `.env`, captured during `resume onboard`. Results are injected as
**research context for tailoring guidance only** — never as resume facts, so the
grounding check still forces every number/company/date to trace to the store.
`fast` mode never touches the network; if no provider/key is configured, `think`
falls back to the provider's built-in search.

## Distribution

| Tier | Command | Who it's for |
|------|---------|--------------|
| **A — isolated install** | `pipx install .` / `uv tool install .` | anyone with Python; gives a global `resume` |
| **B — frozen binary** | `pyinstaller resume.spec --noconfirm` → `dist/resume` | machines with **no Python** |

The PDF renderer is pure Python (ReportLab) — **no LibreOffice/`soffice`** — so
both tiers run with zero system binaries. The binary bundles `markitdown`,
`reportlab`, `python-docx`, `anthropic`, and `openai` (see `resume.spec`).
Cross-OS binaries (Linux/Windows) are produced by running the same spec on each
OS, e.g. via a GitHub Actions build matrix.

> **Known gap:** `think` now depends on `langgraph` + `langchain-*`, which
> `resume.spec` does not yet collect — so the frozen binary currently supports
> `fast` (and all wiki commands) but not `think`. Add `langgraph`, `langchain`,
> `langchain_anthropic`, and `langchain_openai` to the `collect_all` loop in
> `resume.spec` to close it. Tier A (`pipx`/`uv tool`) is unaffected.

## Notes

- **Extraction**: `bootstrap` uses Microsoft **MarkItDown** to convert your
  resume (PDF, DOCX, PPTX, XLSX, HTML…) into clean Markdown that drops straight
  into `raw/`. Falls back to pypdf if needed. MarkItDown is ~82% F1 on complex
  multi-column/table PDF layouts — acceptable here because bootstrap is
  human-reviewed and raw-grounding catches extraction errors. Scanned/image-only
  PDFs need OCR (the `markitdown-ocr` plugin), not enabled in v1.
- **Determinism**: an LLM is in the loop for compile and tailoring, so runs can
  vary slightly. raw/ never changes; re-running `recompile` regenerates career/.
- **Rendering**: `skill/render.py` produces the `.docx` (python-docx) and `.pdf`
  (ReportLab) from the *same* YAML with two independent renderers — no
  DOCX→PDF conversion. It sorts roles reverse-chronologically, keeps the
  Education block together (never split across a page), spaces skill groups for
  readability, and deterministically strips the em-dash "AI tell" (` — ` → ` - `)
  from all content. The sanitize runs in `finalize`/`run_once` *before* the YAML
  is written, so the saved artifact is clean too.
- **Growth path (structured, not built)**: the renderer is a self-contained skill;
  new capabilities (apply to jobs) become declared tools; keep any final "submit"
  step human-gated.
