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
or just:  python cli.py ...
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

app = typer.Typer(add_completion=False, help="Generate tailored resumes from your career store.")
console = Console()


def _report(res: dict):
    console.print()
    if res["ok"]:
        console.print("[green]✓ Grounding check passed[/] — all facts trace to the store.")
    else:
        console.print("[yellow]⚠ Grounding flags[/] (review before sending):")
        for p in res["problems"]:
            console.print(f"   • {p}")
        console.print("[dim]  Some may be false positives (a metric reworded into words).[/]")
    if res.get("used_web"):
        console.print("[cyan]• Web search was used to research the brief.[/]")

    t = Table(show_header=False, box=None, pad_edge=False)
    t.add_row("[bold]YAML[/]", res["yaml"])
    if res.get("pdf"):
        t.add_row("[bold]PDF[/]", res["pdf"])
        t.add_row("[bold]DOCX[/]", res["docx"])
    console.print(t)


def _run(mode: str, brief: str, out: str | None):
    from core import store as _store
    if _store.career_is_empty():
        console.print("[yellow]No career wiki yet.[/] Run "
                      "[bold]resume bootstrap <your-resume.pdf>[/] first.")
        raise typer.Exit()
    cfg = ensure_config()
    m = cfg["modes"][mode]
    out = out or mode
    provider = make_provider(cfg["provider"])
    console.print(Panel.fit(
        f"[bold]{mode}[/]  provider=[cyan]{cfg['provider']}[/]  "
        f"model=[cyan]{m['model']}[/]  web={'[green]on[/]' if m['allow_web'] else '[dim]off[/]'}",
        border_style="blue"))

    accumulated = brief or ""
    if m.get("conversational"):
        console.print("[dim]Think mode — refine the brief across lines. "
                      "Type 'go' to generate, 'quit' to abort.[/]\n")
        if accumulated:
            console.print(f"[dim]current brief:[/] {accumulated}\n")
        while True:
            line = Prompt.ask("[bold cyan]you[/]").strip()
            if line.lower() in ("go", "generate", ""):
                if accumulated:
                    break
                console.print("[yellow]brief is empty — add something first[/]")
                continue
            if line.lower() in ("quit", "exit"):
                console.print("[dim]aborted.[/]")
                raise typer.Exit()
            accumulated = (accumulated + " " + line).strip()
            console.print(f"[dim]brief is now:[/] {accumulated}\n")

    with console.status("[bold]Generating tailored resume…[/]", spinner="dots"):
        res = agent.run_once(provider, model=m["model"], brief=accumulated,
                             allow_web=m["allow_web"], out_stem=out)
    _report(res)


@app.command()
def onboard():
    """(Re)run setup."""
    from core.config import onboard as run_onboard, load_dotenv
    load_dotenv()
    run_onboard()


@app.command()
def fast(brief: str = typer.Argument(..., help="target role / brief text"),
         out: str = typer.Option(None, "--out", help="output filename stem")):
    """Quick one-shot: cheap model, no web search, no conversation."""
    _run("fast", brief, out)


@app.command()
def think(brief: str = typer.Argument("", help="target role / brief text"),
          file: str = typer.Option(None, "--file", help="read brief from a file (JD)"),
          out: str = typer.Option(None, "--out", help="output filename stem")):
    """Strong model, web search, conversational refinement."""
    if file:
        brief = open(file).read()
    _run("think", brief, out)


@app.command()
def status():
    """Show current configuration."""
    cfg = load_config()
    if not cfg:
        console.print("[yellow]No config yet — run:[/] resume onboard")
        raise typer.Exit()
    t = Table(title="Resume Agent config")
    t.add_column("setting"); t.add_column("value")
    t.add_row("provider", cfg["provider"])
    for mode, m in cfg["modes"].items():
        t.add_row(f"{mode}.model", m["model"])
        t.add_row(f"{mode}.web", str(m["allow_web"]))
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
    m = model or cfg["modes"]["think"]["model"]
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


@app.command()
def add(opening: str = typer.Argument("", help="what you want to add (free text)"),
        model: str = typer.Option(None, "--model")):
    """Conversationally add a new project / certificate / skill, then recompile."""
    from core import ingest, compiler
    cfg = ensure_config()
    m = model or cfg["modes"]["think"]["model"]
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
    m = model or cfg["modes"]["think"]["model"]
    provider = make_provider(cfg["provider"])
    with console.status("[bold]Recompiling…[/]", spinner="dots"):
        res = compiler.compile_wiki(provider, model=m)
    console.print("[green]✓ done[/]" if res["ok"] else "[yellow]⚠ flags:[/]")
    for p in res["problems"]:
        console.print(f"   • {p}")


if __name__ == "__main__":
    app()
