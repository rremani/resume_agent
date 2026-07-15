"""
Inline, Claude-style conversational REPL for Resume Agent.

Bare `resume` opens this persistent chat: describe a role or paste a JD and it
tailors your resume; slash commands (/think /fast /add /help /quit) switch modes.
It renders inline — messages and rich blocks scroll into native scrollback above
a pinned prompt_toolkit input box, with a Rich spinner while it works.

Everything plugs into the existing workflows via their callback seams
(`run_think`, `agent.run_once`, `ingest.add_interactive`) — no workflow changes.
The flow is turn-based, so it runs on one thread; the single live status is
always stopped before any output or prompt.
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory


# ---- block builders (pure → unit-testable) -----------------------------

def section_block(title: str, body: str) -> Panel:
    """One section (or the menu) as a titled panel — the focused review unit."""
    return Panel(Text(body or ""), title=title, title_align="left",
                 border_style="cyan", padding=(0, 1))


def result_block(res: dict) -> Panel:
    t = Text()
    if res.get("ok"):
        t.append("✓ Grounding passed — every fact traces to your store.\n", style="green")
    else:
        t.append("⚠ Grounding flags (review before sending):\n", style="yellow")
        for p in (res.get("problems") or [])[:6]:
            t.append(f"   • {p}\n")
    # session-provided (gap-conversation) facts: used but not from the verified
    # wiki — surfaced so the user knows exactly what to double-check.
    if res.get("estimates"):
        t.append("\n⚠ Some numbers are ESTIMATES you asked me to fill in — verify every "
                 "metric before sending.\n", style="yellow")
    provided = res.get("provided") or []
    if provided:
        t.append("\nⓘ you provided these this session (not from your verified wiki):\n",
                 style="cyan")
        for p in provided[:8]:
            t.append(f"   • {p}\n", style="cyan")
    t.append("\n")
    for label in ("yaml", "pdf", "docx"):
        if res.get(label):
            t.append(f"{label.upper():5}", style="bold")
            t.append(res[label] + "\n")
    return Panel(t, title="done", title_align="left",
                 border_style="green", padding=(0, 1))


# ---- the REPL ----------------------------------------------------------

COMMANDS = {
    "think": "tailor a resume (deep) — the default when you just type a role/JD",
    "fast": "quick one-shot resume from a short brief",
    "add": "capture new experience into your career wiki",
    "help": "show this help",
    "quit": "leave",
}


class ReplApp:
    """Persistent inline chat. Provides the workflow callbacks and dispatches
    top-level input (plain text → deep tailor; /cmd → mode)."""

    def __init__(self, cfg: dict, out_stem: str = "think"):
        self.cfg = cfg
        self.out_stem = out_stem
        self.console = Console()
        self._status = None
        self._session = None   # created lazily on first prompt (needs a TTY)

    # -- status (single live spinner spanning one thinking phase) --
    def _stop_status(self):
        if self._status is not None:
            self._status.stop()
            self._status = None

    def notify(self, text: str):
        self._stop_status()
        self._status = self.console.status(f"[dim]· {text}[/]", spinner="dots")
        self._status.start()

    # -- output callbacks --
    def say(self, text: str):
        self._stop_status()
        self.console.print(Text(text, style="magenta"))

    def on_section(self, title: str, body: str):
        self._stop_status()
        self.console.print(section_block(title, body))

    def render(self, data: dict, stem: str):
        from core import agent as _agent
        return _agent.render(data, stem)

    # -- input (read_line is the only prompt_toolkit touch; stubbed in tests) --
    def read_line(self, placeholder: str) -> str:
        if self._session is None:
            self._session = PromptSession(history=InMemoryHistory())
        return self._session.prompt(
            HTML("<ansicyan><b>› </b></ansicyan>"),
            placeholder=HTML(f"<ansibrightblack>{placeholder}</ansibrightblack>"),
        )

    def ask(self, placeholder: str = "type your reply…") -> str:
        self._stop_status()
        try:
            return (self.read_line(placeholder) or "").strip()
        except (KeyboardInterrupt, EOFError):
            return "quit"

    # -- modes --
    def think(self, brief: str) -> dict:
        from core import think_graph
        allow_web = bool(self.cfg.get("allow_web", True))
        self._stop_status()
        try:
            res = think_graph.run_think(
                self.cfg, opening=brief or "", out_stem=self.out_stem,
                ask_fn=self.ask, say_fn=self.say, notify_fn=self.notify,
                render_fn=self.render, allow_web=allow_web, on_section=self.on_section)
        except KeyboardInterrupt:
            self._stop_status()
            self.console.print("[dim]aborted.[/]")
            return {"aborted": True}
        finally:
            self._stop_status()
        if res.get("aborted"):
            self.console.print("[dim]aborted — nothing generated.[/]")
        else:
            self.console.print(result_block(res))
        return res

    def fast(self, brief: str) -> dict:
        from core import agent as _agent
        from core.providers import make_provider
        provider = make_provider(self.cfg["provider"])
        self.notify("generating (fast)…")
        res = _agent.run_once(provider, model=self.cfg["model"], brief=brief,
                              allow_web=bool(self.cfg.get("allow_web", True)), out_stem="fast")
        self._stop_status()
        self.console.print(result_block(res))
        return res

    def add(self, text: str):
        from core import ingest, compiler
        from core.providers import make_provider
        provider = make_provider(self.cfg["provider"])
        model = self.cfg["model"]
        if not text:
            text = self.ask("what would you like to add?")
        if text.lower() in ("quit", "exit", ""):
            return
        path = ingest.add_interactive(provider, model=model, opening=text,
                                      ask_fn=self.ask, say_fn=self.say)
        if not path:
            self.say("cancelled — nothing saved.")
            return
        self.notify("recompiling your wiki…")
        compiler.compile_wiki(provider, model=model)
        self._stop_status()
        self.say(f"✓ saved and recompiled: {path}")

    # -- dispatch + loop --
    def _help(self):
        self._stop_status()
        t = Text()
        t.append("Just type a role or paste a JD to tailor your resume.\n\n", style="dim")
        for name, desc in COMMANDS.items():
            t.append(f"  /{name:6}", style="cyan")
            t.append(desc + "\n")
        self.console.print(Panel(t, title="help", title_align="left",
                                 border_style="magenta", padding=(0, 1)))

    def _dispatch(self, line: str) -> bool:
        """Return False to quit the REPL."""
        low = line.lower()
        if low in ("quit", "exit"):
            return False
        if not line:
            return True
        if line.startswith("/"):
            cmd, _, rest = line[1:].partition(" ")
            cmd, rest = cmd.lower(), rest.strip()
            if cmd in ("quit", "exit", "q"):
                return False
            if cmd in ("help", "h", "?"):
                self._help()
            elif cmd == "think":
                self.think(rest)
            elif cmd == "fast":
                if rest:
                    self.fast(rest)
                else:
                    self.say("usage: /fast <role or brief>")
            elif cmd == "add":
                self.add(rest)
            else:
                self.say(f"unknown command /{cmd} — try /help")
            return True
        # plain text → default deep tailor
        self.think(line)
        return True

    def _greet(self):
        model = self.cfg["model"]
        web = bool(self.cfg.get("allow_web", True))
        hdr = Text()
        hdr.append("● ", style="bold green")
        hdr.append("Resume Agent", style="bold")
        hdr.append(f"  · {model}" + (" · researches the web" if web else ""), style="dim")
        msg = Text(
            "Hi! Tell me the role you're targeting — paste a job description, or "
            "describe the role and company. I'll research it, work out what gets "
            "shortlisted, and build the strongest resume your real experience "
            "supports. I never invent experience you don't have.",
            style="magenta")
        hint = Text("/help for commands · /quit to leave", style="dim")
        return hdr, msg, hint

    def loop(self):
        hdr, fallback, hint = self._greet()
        self.console.print()
        self.console.print(hdr)          # header shows instantly
        self.console.print()
        # LLM writes the opening line (aware of your background); fall back to the
        # static message on any error (no key, offline, etc.).
        msg = fallback
        self.notify("getting ready…")
        try:
            from core import think_graph
            text = think_graph.session_greeting(self.cfg)
            if text:
                msg = Text(text, style="magenta")
        except Exception:
            pass
        self._stop_status()
        self.console.print(msg)
        self.console.print()
        self.console.print(hint)
        while True:
            line = self.ask("paste a JD or describe the role…")
            try:
                if not self._dispatch(line):
                    break
            except KeyboardInterrupt:
                self._stop_status()
                self.console.print("[dim]interrupted — /quit to leave.[/]")
        self.console.print("[dim]bye.[/]")


def run_repl(cfg: dict):
    """Bare `resume` → persistent inline chat."""
    ReplApp(cfg).loop()


def run_think_tui(cfg: dict, *, opening: str, out_stem: str, allow_web: bool) -> dict:
    """One-shot `resume think "..."` → inline think flow, then exit."""
    return ReplApp(cfg, out_stem=out_stem).think(opening)
