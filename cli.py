#!/usr/bin/env python3
"""
Resume Agent — terminal CLI (Typer + Rich).

  resume onboard                      (re)run setup
  resume fast  "<brief>"  [--out X]   quick one-shot: cheap model, no web
  resume think "<brief>"  [--out X]   strong model, web search, conversation
  resume think --file jd.txt
  resume status                       show current config

First run auto-launches onboarding. Config in config.yaml; API keys read from
env (ANTHROPIC_API_KEY / OPENROUTER_API_KEY), never stored.

Install the console command with:  pip install -e .   then run `resume ...`
or just:  resume ...
"""
from __future__ import annotations
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt

from core.config import ensure_config, load_config
from core.providers import make_provider
from core import agent

app = typer.Typer(add_completion=False, help=(
    "Run `resume` (no arguments) to open the chat and tailor your resume. "
    "The commands below are for setup and maintenance."))
console = Console()


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context):
    """Bare `resume` opens the interactive chat (type a role or paste a JD).
    Subcommands (think / fast / add / onboard / bootstrap / recompile) still work."""
    if ctx.invoked_subcommand is not None:
        return
    import sys
    cfg = ensure_config()
    if not _ensure_wiki(cfg):
        raise typer.Exit()
    if not sys.stdout.isatty():
        console.print('The interactive chat needs a terminal — use '
                      '[bold]resume think "<brief>"[/] or [bold]resume fast "<brief>"[/].')
        raise typer.Exit()
    from tui import run_repl
    run_repl(cfg)


def _report(res: dict):
    console.print()
    if res["ok"]:
        console.print("[green]✓ Grounding check passed[/] — all facts trace to the store.")
    else:
        console.print("[yellow]⚠ Grounding flags[/] (review before sending):")
        for p in res["problems"]:
            console.print(f"   • {p}")
        console.print("[dim]  Some may be false positives (a metric reworded into words).[/]")
    if res.get("used_search"):
        console.print("[cyan]• Explicit search tool researched the target (see RESEARCH CONTEXT).[/]")
    if res.get("used_web"):
        console.print("[cyan]• Provider built-in web search was used.[/]")

    t = Table(show_header=False, box=None, pad_edge=False)
    t.add_row("[bold]YAML[/]", res["yaml"])
    if res.get("pdf"):
        t.add_row("[bold]PDF[/]", res["pdf"])
        t.add_row("[bold]DOCX[/]", res["docx"])
    console.print(t)


def _check_staleness(cfg, provider, auto: bool):
    """Warn (or auto-recompile) if raw/ changed since the wiki was last built."""
    from core import store as _store, compiler
    if not _store.wiki_is_stale():
        return
    c = _store.raw_changes()
    if not c["has_manifest"]:
        msg = ("change-tracking not initialized yet for this wiki — run "
               "[bold]resume recompile[/] once to enable it")
    else:
        bits = []
        for label, key in (("modified", "modified"), ("added", "added"),
                           ("removed", "removed")):
            if c[key]:
                bits.append(f"{len(c[key])} {label}")
        msg = (f"[yellow]⚠ raw/ changed since last compile[/] ({', '.join(bits)}) "
               "— the career wiki may be out of date.")
    if auto:
        console.print(f"{msg}\n[dim]--auto: recompiling…[/]")
        with console.status("[bold]Recompiling career wiki…[/]", spinner="dots"):
            res = compiler.compile_wiki(provider, model=cfg["model"])
        console.print("[green]✓ wiki recompiled[/]" if res["ok"]
                      else "[yellow]⚠ recompiled with grounding flags[/]")
    else:
        console.print(msg)
        console.print("[dim]  run [bold]resume recompile[/] to re-sync "
                      "(or pass [bold]--auto[/] to recompile automatically).[/]\n")


def _ensure_wiki(cfg) -> bool:
    """No career wiki yet? Offer to build it inline instead of dead-ending the
    user to a separate command. Returns True once a wiki exists."""
    import os
    from core import store as _store, config as _config
    if not _store.career_is_empty():
        return True

    if _store.raw_is_empty():
        # No sources at all → build from an existing resume now (same flow as
        # onboarding), rather than bouncing to `resume bootstrap`.
        console.print("[yellow]No career wiki yet.[/] Let's build one from your "
                      "existing resume.")
        env = _config.ENV_VAR.get(cfg["provider"])
        key_present = (not env) or bool(os.environ.get(env))
        _config._maybe_bootstrap(cfg, key_present=key_present)
    else:
        # Sources exist but were never compiled → recompile, don't re-bootstrap.
        console.print("[yellow]Your raw sources aren't compiled into a wiki yet.[/]")
        env = _config.ENV_VAR.get(cfg["provider"])
        if env and not os.environ.get(env):
            console.print("[dim]Set your API key, then run [bold]resume recompile[/].[/]")
        elif Prompt.ask("Compile them now?", default="y").strip().lower().startswith("y"):
            from core import compiler
            with console.status("[bold]Compiling career wiki…[/]", spinner="dots"):
                compiler.compile_wiki(make_provider(cfg["provider"]),
                                      model=cfg["model"])

    if _store.career_is_empty():
        console.print("[dim]Still no wiki — run [bold]resume bootstrap "
                      "<your-resume>[/] when you're ready.[/]")
        return False
    return True


def _run(mode: str, brief: str, out: str | None, auto: bool = False,
         web: bool = False, plain: bool = False):
    cfg = ensure_config()
    if not _ensure_wiki(cfg):
        raise typer.Exit()
    out = out or mode
    provider = make_provider(cfg["provider"])

    _check_staleness(cfg, provider, auto)

    if mode == "think":
        _run_think(cfg, provider, brief, out, plain=plain)
        return

    # fast: single-shot, same model as think. Web follows config (or --web override).
    allow_web = bool(web) or bool(cfg.get("allow_web", True))
    search_tool = None
    if allow_web:
        from core import search as _search
        search_tool = _search.from_config(cfg)
    search_max = ((cfg.get("search") or {}).get("max_results")) or 3
    search_name = (cfg.get("search") or {}).get("provider") if search_tool else None

    web_label = "[green]on[/]" if allow_web else "[dim]off[/]"
    search_label = f"[cyan]{search_name}[/]" if search_tool else "[dim]built-in[/]"
    console.print(Panel.fit(
        f"[bold]fast[/]  provider=[cyan]{cfg['provider']}[/]  "
        f"model=[cyan]{cfg['model']}[/]  web={web_label}  search={search_label}",
        border_style="blue"))

    with console.status("[bold]Generating tailored resume…[/]", spinner="dots"):
        res = agent.run_once(provider, model=cfg["model"], brief=brief or "",
                             allow_web=allow_web, out_stem=out,
                             search_tool=search_tool, search_max_results=search_max)
    _report(res)


def _run_think(cfg, provider, brief, out, plain: bool = False):
    """JD-driven tailoring workflow: understand → assess → convo → fill → improve
    → finalize (grounded render)."""
    import sys
    allow_web = bool(cfg.get("allow_web", True))

    # Inline Claude-style TUI by default in a real terminal; fall back to the
    # plain flow when piped/non-TTY or --plain (a pinned-input REPL needs a TTY).
    if not plain and sys.stdout.isatty():
        from tui import run_think_tui
        res = run_think_tui(cfg, opening=brief or "", out_stem=out, allow_web=allow_web)
        if res.get("aborted"):
            raise typer.Exit()
        return

    # Print the banner BEFORE importing think_graph: that import pulls in
    # langgraph/langchain (~1.5s), so showing the banner first means the terminal
    # isn't frozen while it loads.
    web_state = "[green]on[/]" if allow_web else "[dim]off[/]"
    console.print(Panel.fit(
        f"[bold]think[/]  model=[cyan]{cfg['model']}[/]  web={web_state}",
        border_style="blue"))
    console.print("[dim]It tailors a resume from your verified experience, shows a "
                  "preview, and refines on your changes.\n"
                  "Reply with changes · 'done' to generate · 'quit' to abort.[/]\n")

    with console.status("[dim]starting up…[/]", spinner="dots"):
        from core import think_graph, agent as _agent

    def ask_fn():
        return Prompt.ask("[bold cyan]you[/]")

    def say_fn(text):
        console.print(f"[bold magenta]agent[/]: {text}\n")

    def notify_fn(text):
        console.print(f"[dim]· {text}[/]")

    def render_fn(data, stem):
        return _agent.render(data, stem)

    res = think_graph.run_think(
        cfg, opening=brief or "", out_stem=out, ask_fn=ask_fn, say_fn=say_fn,
        notify_fn=notify_fn, render_fn=render_fn, allow_web=allow_web)
    if res.get("aborted"):
        console.print("[dim]aborted — nothing generated.[/]")
        raise typer.Exit()
    _report(res)


@app.command()
def onboard():
    """(Re)run setup."""
    from core.config import onboard as run_onboard, load_dotenv
    load_dotenv()
    run_onboard()


@app.command(hidden=True)   # primary surface is /fast inside the `resume` chat
def fast(brief: str = typer.Argument(..., help="target role / brief text"),
         out: str = typer.Option(None, "--out", help="output filename stem"),
         web: bool = typer.Option(False, "--web",
                                  help="allow web research this run (default offline)"),
         auto: bool = typer.Option(False, "--auto",
                                   help="auto-recompile if raw/ changed")):
    """Quick one-shot: cheap model, single-shot. Offline unless --web."""
    _run("fast", brief, out, auto, web)


@app.command(hidden=True)   # primary surface is `resume` (or /think inside the chat)
def think(brief: str = typer.Argument("", help="target role / brief text"),
          file: str = typer.Option(None, "--file", help="read brief from a file (JD)"),
          out: str = typer.Option(None, "--out", help="output filename stem"),
          auto: bool = typer.Option(False, "--auto",
                                    help="auto-recompile if raw/ changed"),
          plain: bool = typer.Option(False, "--plain",
                                     help="disable the interactive TUI (plain flow)")):
    """Tailors your verified experience to a target in an interactive TUI: drafts,
    shows a live preview, refines on your feedback, then writes the grounded resume."""
    if file:
        brief = open(file).read()
    _run("think", brief, out, auto, plain=plain)


@app.command()
def status():
    """Show current configuration."""
    from core.config import migrate
    cfg = load_config()
    if not cfg:
        console.print("[yellow]No config yet — run:[/] resume onboard")
        raise typer.Exit()
    cfg = migrate(cfg)
    t = Table(title="Resume Agent config")
    t.add_column("setting"); t.add_column("value")
    t.add_row("provider", cfg["provider"])
    t.add_row("model", cfg.get("model", "—"))
    t.add_row("web", str(cfg.get("allow_web", True)))
    search = (cfg.get("search") or {}).get("provider", "built-in")
    t.add_row("search", search)
    console.print(t)


@app.command()
def bootstrap(doc: str = typer.Argument(..., help="path to your existing resume (PDF, DOCX, etc.)"),
              model: str = typer.Option(None, "--model", help="override compile model")):
    """First run: turn an existing resume into the raw + compiled career wiki."""
    from core import ingest, store as _store
    from core.extract import extract_to_markdown
    if not _store.raw_is_empty():
        console.print("[yellow]raw/ already has sources.[/] Use [bold]add[/] to extend, "
                      "or clear raw/ and career/ to re-bootstrap.")
        raise typer.Exit()
    cfg = ensure_config()
    m = model or cfg["model"]
    provider = make_provider(cfg["provider"])

    try:
        resume_text, method = extract_to_markdown(doc)
    except FileNotFoundError:
        console.print(f"[red]File not found:[/] {doc}")
        raise typer.Exit(1)
    if not resume_text.strip():
        console.print("[red]Could not extract any text.[/] If this is a scanned/image "
                      "PDF, it needs OCR (markitdown-ocr), which isn't enabled in v1.")
        raise typer.Exit(1)

    console.print(Panel.fit(f"Bootstrapping career wiki from [cyan]{doc}[/]\n"
                            f"extracted via [cyan]{method}[/], compiling with [cyan]{m}[/]",
                            border_style="blue"))
    with console.status("[bold]Compiling raw → career wiki…[/]", spinner="dots"):
        res = ingest.bootstrap_from_text(provider, model=m, resume_text=resume_text)
    if res["ok"]:
        console.print("[green]✓ Wiki compiled[/] — every fact traces to the raw resume.")
    else:
        console.print("[yellow]⚠ Compile grounding flags:[/]")
        for p in res["problems"]:
            console.print(f"   • {p}")
    console.print("[dim]raw/ holds the immutable original; career/ holds the wiki.[/]")


@app.command(hidden=True)   # primary surface is /add inside the `resume` chat
def add(opening: str = typer.Argument("", help="what you want to add (free text)"),
        model: str = typer.Option(None, "--model")):
    """Conversationally add a new project / certificate / skill, then recompile."""
    from core import ingest, compiler
    cfg = ensure_config()
    m = model or cfg["model"]
    provider = make_provider(cfg["provider"])

    if not opening:
        opening = Prompt.ask("[bold cyan]What would you like to add?[/]")

    console.print("[dim]The agent will ask a few questions. Type 'quit' to cancel.[/]\n")

    def ask_fn():
        return Prompt.ask("[bold cyan]you[/]")
    def say_fn(text):
        console.print(f"[bold magenta]agent[/]: {text}\n")

    path = ingest.add_interactive(provider, model=m, opening=opening,
                                  ask_fn=ask_fn, say_fn=say_fn)
    if not path:
        console.print("[dim]cancelled — nothing saved.[/]")
        raise typer.Exit()
    console.print(f"\n[green]✓ Saved raw entry:[/] {path}")
    with console.status("[bold]Recompiling career wiki…[/]", spinner="dots"):
        res = compiler.compile_wiki(provider, model=m)
    if res["ok"]:
        console.print("[green]✓ Wiki updated[/] — facts still trace to raw sources.")
    else:
        console.print("[yellow]⚠ Compile grounding flags:[/]")
        for p in res["problems"]:
            console.print(f"   • {p}")


@app.command()
def recompile(model: str = typer.Option(None, "--model")):
    """Rebuild the career wiki from raw sources (no new input)."""
    from core import compiler
    cfg = ensure_config()
    m = model or cfg["model"]
    provider = make_provider(cfg["provider"])
    with console.status("[bold]Recompiling…[/]", spinner="dots"):
        res = compiler.compile_wiki(provider, model=m)
    console.print("[green]✓ done[/]" if res["ok"] else "[yellow]⚠ flags:[/]")
    for p in res["problems"]:
        console.print(f"   • {p}")


if __name__ == "__main__":
    app()
