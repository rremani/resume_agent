# Think mode — Claude-style inline conversational TUI

Date: 2026-07-11
Status: approved (design), pending spec review

## Why

The current `think` interface (Typer + Rich `Prompt.ask`) feels, per the user's
own words, plain, non-interactive, and clunky. The target feel is **how Claude
Code works**: a persistent, *inline* conversational REPL — messages scroll into
your normal terminal buffer (scrollback preserved), a styled input box stays
pinned at the bottom, a spinner runs while the agent thinks, and rich blocks
(strategy, preview) render inline in the conversation.

This changes only the **interface**. The `run_think` LangGraph workflow,
grounding, wiki, and render are untouched — the TUI plugs into the existing
callback seam (`ask_fn` / `say_fn` / `notify_fn` / `render_fn`).

## Tool choice — Rich + prompt_toolkit, NOT Textual

Earlier in discussion I leaned Textual. On pinning the model to *inline +
native-scrollback + persistent bottom input*, that's the wrong tool:

- **Textual** is a full-screen (alt-screen) app framework; its strength is
  dashboards (which we dropped). Its `inline=True` mode renders a *fixed-height*
  region with internal scrolling — it does **not** grow into the terminal's
  native scrollback. That's not the Claude model.
- **prompt_toolkit** is purpose-built for inline REPLs: a pinned input box with
  line editing / history / placeholder, and `patch_stdout` to print above the
  prompt into native scrollback. This is exactly Claude Code's inline behaviour.
- **Rich** (already a dependency) renders the styled message/strategy/preview
  blocks and the "thinking" spinner (`console.status`, animated on a background
  thread so it spins *during* a blocking LLM call).

Decision: **Rich (output blocks + spinner) + prompt_toolkit (input box)**, both
inline, pure-Python, bundle fine in the frozen binary. No core rewrite.

Alternative considered: Rich only (plain `Prompt.ask`, no box/history). Rejected
— the input box + history is a defining part of the Claude feel and Rich has no
interactive input widget.

## Interaction model (sequential, no thread bridge)

The flow is **turn-based**: the agent talks, then asks, then waits for input —
input is only needed at defined points (the `understand` question and each
`improve` round). While the agent "thinks" (blueprint / fill / edit), no input
is needed — just a spinner. Because it's sequential, the graph runs on the main
thread and **no worker-thread/queue bridge is required**:

- The spinner animates *during* a blocking LLM node because Rich's
  `console.status` refreshes on its own background thread while the main thread
  blocks on the call.
- `ask_fn` is a blocking `prompt_toolkit` read on the same thread.

The REPL holds a single live-status handle. Any output callback stops the
running status before printing, so the spinner spans exactly one thinking phase
(from the `notify_fn` that starts it to the next block/question).

```
● Resume Agent — think · target: LLM Engineer · gemini-3.5-flash

  What role or company are you targeting?
› llm engineer at a fintech

  · planning the resume…                     ← animated status (Rich)

  Strategy — leading with
    1 Agentic AI & LLM orchestration
    2 Production ML at scale
    3 Data platforms

  Draft ready · 5 roles · summary tuned for LLM Engineering
  ┌ preview ─────────────────────────────────────┐
  │ Raja Raghudeep Emani                          │
  │ ▸ Data Scientist — SAAL.AI      03/23–Present │
  │   • Built agentic proposal-gen…               │
  └───────────────────────────────────────────────┘

  Anything to change?  ('done' to generate · 'quit' to abort)
› ▏                                              ← pinned prompt_toolkit box
```

## Components

### New: `tui.py` (top-level, sibling of `cli.py`)
A `ThinkREPL` that provides the callbacks to `run_think`:

- `say_fn(text)` — stop status; render an agent message block (Rich) into
  scrollback.
- `notify_fn(text)` — stop prior status; start an animated `· {text}…` status
  that persists through the blocking node work.
- `on_draft(draft, blueprint)` — **new callback**; stop status; render the
  Strategy block (from `blueprint`) and the Preview block (from `draft`) as
  styled Rich panels.
- `ask_fn()` — stop status; show the prompt_toolkit box (`› `, placeholder,
  history, Enter submits); return the line. Ctrl-C / Ctrl-D → treated as quit.
- `render_fn(data, stem)` — unchanged (in-process render).

A thin `read_line(prompt, placeholder)` seam wraps prompt_toolkit so tests can
stub input without a real terminal.

### Changed: `core/think_graph.py` — `run_think`
Add one optional param `on_draft=None`. In the `improve` node:

```python
if on_draft:
    on_draft(state["draft"], state.get("blueprint"))   # rich blocks
else:
    say_fn(preview(state["draft"], state.get("blueprint")))   # plain text
say_fn("Anything to change? …")
```

So plain mode is byte-for-byte unchanged; the TUI gets structured data to render
richly. No other graph changes.

### Changed: `cli.py` — `think` command
- Add `--plain` flag.
- Launch the TUI when `sys.stdout.isatty()` and not `--plain`; otherwise run the
  existing plain `_run_think`. This auto-fallback is **required**, not optional:
  a pinned-input REPL needs a real TTY, so piped output / CI must use plain.

## Scope

- **v1: `think` only.** `fast` stays plain — it's single-shot, no conversation.
- Auto-fallback to plain on non-TTY or `--plain`.
- **Deferred (v2):** diff-highlighting of what changed each round; type-while-
  thinking concurrency (needs `patch_stdout` + a worker thread); a TUI for
  `fast` / `add`; theming.

## Dependencies

- Add `prompt_toolkit>=3.0` to `requirements.txt` (Rich already present).
- Add `prompt_toolkit` to the `collect_all` loop in `resume.spec` for the frozen
  binary. (Unrelated pre-existing gap: langgraph/langchain also missing from the
  spec — tracked separately.)

## Testing

- `run_think`: `on_draft` is invoked with `(draft, blueprint)` in `improve`, and
  when `on_draft` is provided the plain-text preview is **not** also emitted via
  `say_fn` (stub callbacks; assert call routing).
- `tui`: pure pieces are unit-tested behind seams — the Strategy/Preview block
  builders (given a draft+blueprint dict → expected Rich renderable/text), and
  the status start/stop routing. Input is exercised via a stubbed `read_line`, so
  a scripted `["done"]` drives a stubbed graph to completion with no real TTY.
- Plain mode: existing tests stay green (the `on_draft=None` path is unchanged).

## Rollback

Contained: new `tui.py`, one optional `run_think` param (defaults `None` → no
behaviour change), and one `cli.py` branch + flag. Delete `tui.py` and the
branch to revert entirely.

## Risks

- **Rich ↔ prompt_toolkit cooperation.** Because the flow is sequential (status
  is always stopped before the prompt is shown), Rich never prints while the
  prompt is live — no contention, no need for `patch_stdout` in v1. If v2 adds
  type-while-thinking, that's when `patch_stdout` becomes necessary.
- **Frozen binary.** prompt_toolkit bundles cleanly; verify in the Tier-B build
  (which already has the separate langchain gap to fix).
- **Terminal quirks.** Non-TTY is handled by auto-fallback; unusual terminals
  degrade to prompt_toolkit's own fallbacks.
