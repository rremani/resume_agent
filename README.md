<h1 align="center">Resume Agent</h1>

<p align="center">
  <b>The résumé tailor that won't lie for you.</b><br>
  A terminal-native AI agent that builds a knowledge base from your <i>real</i> experience
  and tailors it to each job — analyzing gaps instead of inventing them.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg" alt="Platforms">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License: MIT">
</p>

<!-- TODO: record a ~40s terminal demo (asciinema or GIF) and embed it here. -->

---

## Why it's different

Most AI resume tools happily **fabricate** — inflating titles, inventing metrics — to
match a job description. Resume Agent won't. It keeps a **grounded knowledge base** of
your real experience and, for each target, tells you honestly what fits and what's
**missing** — then tailors within the truth of what you've actually done.

- 🔒 **Never fabricates** — every number, title, and date traces back to your own sources.
- 🎯 **JD-driven** — reads each posting for what gets shortlisted, and surfaces *your* gaps.
- 🗣️ **Fills gaps by asking** — pulls in real experience you never captured (you decide, it flags what to verify).
- 🧠 **Compounding memory** — a career "wiki" that grows every time you add experience.
- 💻 **Terminal-native & local** — your data stays on your machine; bring your own API key.
- 📄 **Real output** — clean **PDF + DOCX**, rendered deterministically (no LibreOffice).

## Install

Clone the repo and run one script — it checks Python, installs
[uv](https://docs.astral.sh/uv/) if needed, and installs the global `resume` command
with all dependencies.

```bash
# macOS / Linux   (Windows: use Git Bash or WSL, then the same command)
./start.sh

# Windows (native PowerShell)
powershell -ExecutionPolicy Bypass -File start.ps1
```

Open a new terminal and type `resume`. The first run walks you through setup and can
build your knowledge base from an existing resume.

**Requirements:** Python 3.10+ and an API key from [Anthropic](https://console.anthropic.com)
or [OpenRouter](https://openrouter.ai) (kept in a git-ignored `.env`, never uploaded).

## Use it

Just run **`resume`** — it opens a chat:

```
› resume

● Resume Agent
  Tell me the role you're targeting — paste a job description or describe it.

› <paste a JD, or "senior GenAI engineer at a fintech">
```

Then it:

1. **Analyzes the JD** against your experience — what gets shortlisted, and where you
   have **gaps** (both missing requirements *and* weak bullets, e.g. claims with no numbers).
2. **Talks to you** to fill real gaps — asks for genuine experience or metrics you have
   but never captured. Used for this resume only; never fabricated on its own.
3. **Builds** the resume, then lets you **review it section by section** — open a section,
   change it in plain language (`drop 2`, `tighten 1`, `reword 3`), go `back`, or `done`
   to generate the PDF + DOCX.

Inside the chat: `/think` (deep, default) · `/fast` (quick one-shot) · `/add` (capture new
experience) · `/help` · `/quit`.

Maintenance commands live next to `resume`:

```bash
resume onboard                 # (re)run setup
resume bootstrap my_resume.pdf # build the knowledge base from an existing resume
resume recompile               # rebuild it from your raw sources
resume status                  # show config
```

## How it stays honest

Facts live in a two-tier store, so the resume can never drift your numbers:

```
raw/       your immutable sources (original resume + anything you add later)
career/    a compiled "wiki" (roles, projects, skills) built from raw/
```

- **`raw/` is append-only** — facts are added, never rewritten.
- Every number/company/date in `career/` must **trace back to `raw/`** (checked at compile
  time), and the tailored resume is **grounded** against the wiki before it renders.
- **Local optimum, not global** — a requirement you don't have is simply not claimed.
  Anything you provide mid-conversation is used for that resume only and flagged as
  *"you provided this,"* so you know what to verify. Ask it to estimate a missing number
  and it will — clearly marked *"verify before sending."*

## Your data

Everything lives under one per-user folder:

```
~/.resume-agent/      (override with $RESUME_AGENT_HOME)
  raw/                immutable sources
  career/             the compiled wiki
  output/             generated resumes (.yaml / .pdf / .docx)
  config.yaml         provider + model + web setting
  .env                your API key (git-ignored, owner-only)
```

Your API key is written to a git-ignored `.env`, never to `config.yaml`. If you hand-edit
`raw/`, the agent notices and offers to `recompile`.

## Config

One model is used for everything (fast and think differ only in whether they converse).
Web research is on by default so `think` can understand the target.

```yaml
provider: openrouter          # anthropic | openrouter
model: google/gemini-3.5-flash
allow_web: true
search:                       # optional explicit search tool for research
  provider: tavily            # tavily | exa | brave | none
```

## Under the hood

`think` is a small [LangGraph](https://langchain-ai.github.io/langgraph/) workflow:

```
understand → assess → convo → fill → refine → finalize
```

The wiki is small, so it's fed whole (no retrieval/RAG). Resumes render to PDF (ReportLab)
and DOCX (python-docx) from the same data — pure Python, no external binaries.

```
core/            store, compiler, providers, search, config, agent, think_graph
skill/render.py  deterministic YAML → PDF + DOCX
tui.py           the inline chat REPL (Rich + prompt_toolkit)
cli.py           command surface (Typer)
```

## Contributing

Issues and PRs welcome. `pip install -r requirements.txt` then `python -m pytest tests/`
to run the suite.

## License

[MIT](LICENSE) — do whatever you like; no warranty.
