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

## Lifecycle

```bash
pip install -r requirements.txt
python cli.py onboard
#   → choose provider (anthropic / openrouter)
#   → pick fast / think models (sensible defaults offered)
#   → paste your API key  → saved to a git-ignored .env (NOT config.yaml)
#   → optionally point it at your existing resume → wiki built on the spot

# If you skipped the resume during onboarding, build it any time:
python cli.py bootstrap my_resume.pdf

# Over time — add new experience conversationally
python cli.py add "finished a consulting project on fraud detection"
#    → the agent asks free-form follow-ups (client? scale? impact? tech? when?)
#    → writes ONE immutable raw entry, then recompiles the affected wiki pages
python cli.py add "got AWS Solutions Architect certification"

# Any time — generate a tailored resume
python cli.py fast  "GenAI role at a bank, emphasize LLM + risk"
python cli.py think --file jd.txt
```

Other commands: `status` (show config), `recompile` (rebuild wiki from raw).

## Secrets

Your API key is captured during onboarding and written to a **git-ignored `.env`**,
never to `config.yaml`. The file is chmod `600` (owner-only) and loaded
automatically on every run, so you set the key once. `.gitignore` keeps `.env`,
`config.yaml`, generated outputs, and your personal `raw/`+`career/` data out of
version control and out of any zip you share.

## Modes

| Mode  | Model        | Web search | Conversation |
|-------|--------------|------------|--------------|
| fast  | cheap (cfg)  | off        | no           |
| think | strong (cfg) | on         | yes          |

Both emit the same YAML schema and pass the same grounding check.

## Providers

`core/providers.py` is one interface over **Anthropic** (native web search) and
**OpenRouter** (`:online` suffix). Switch via `python cli.py onboard`.

## Architecture (files)

```
core/store.py      raw + career read/write, frontmatter parsing
core/extract.py    document → markdown (MarkItDown primary, pypdf fallback)
core/compiler.py   raw → career wiki (LLM), grounded against raw; wiki → resume YAML
core/ingest.py     bootstrap (doc→raw→wiki) and conversational add
core/agent.py      tailoring: wiki → tailored YAML → ground-check → render
core/providers.py  Anthropic + OpenRouter behind one interface
core/config.py     onboarding + config.yaml
skill/render.py    deterministic skill: YAML → fixed-style PDF + DOCX
cli.py             Typer + Rich terminal surface
```

## Notes

- **Extraction**: `bootstrap` uses Microsoft **MarkItDown** to convert your
  resume (PDF, DOCX, PPTX, XLSX, HTML…) into clean Markdown that drops straight
  into `raw/`. Falls back to pypdf if needed. MarkItDown is ~82% F1 on complex
  multi-column/table PDF layouts — acceptable here because bootstrap is
  human-reviewed and raw-grounding catches extraction errors. Scanned/image-only
  PDFs need OCR (the `markitdown-ocr` plugin), not enabled in v1.
- **Determinism**: an LLM is in the loop for compile and tailoring, so runs can
  vary slightly. raw/ never changes; re-running `recompile` regenerates career/.
- **Growth path (structured, not built)**: the renderer is a self-contained skill;
  new capabilities (apply to jobs) become declared tools; keep any final "submit"
  step human-gated.
