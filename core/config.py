"""
Config + onboarding.

First run walks the user through provider, models per mode, API key, and an
optional resume to bootstrap from. Non-secret settings persist to config.yaml.
The API key is written to a git-ignored .env (never to config.yaml), and loaded
automatically on every run. Re-runnable via `resume onboard`.
"""

from __future__ import annotations
import os
import yaml

from . import paths

# Model calls go through LiteLLM, so any LiteLLM provider works. These are the
# common ones we offer by name (env var LiteLLM reads + a suggested model). Any
# other provider works too — the user types a model string and its env var.
ENV_VAR = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "xai": "XAI_API_KEY",
    "ollama": "",           # local, no key
}

PROVIDERS = list(ENV_VAR)   # display order for onboarding


def load_dotenv():
    """Load KEY=VALUE lines from .env into os.environ (without overwriting
    anything already set in the real environment). Called at startup."""
    env_file = paths.env_path()
    if not os.path.exists(env_file):
        return
    for line in open(env_file):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def write_env_var(name: str, value: str):
    """Upsert one KEY=VALUE into .env, preserving other lines. Chmod 600."""
    env_file = paths.env_path()
    paths.ensure_dirs()
    lines = []
    found = False
    if os.path.exists(env_file):
        for line in open(env_file):
            if line.strip().startswith(name + "="):
                lines.append(f"{name}={value}\n")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{name}={value}\n")
    with open(env_file, "w") as f:
        f.writelines(lines)
    try:
        os.chmod(env_file, 0o600)  # owner-only
    except OSError:
        pass
    os.environ[name] = value

# One model per connection. fast and think use the SAME model — the only
# difference is that fast is one-shot and think is conversational. Web research
# is on by default (think uses it to understand the target).
DEFAULTS = {
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "allow_web": True,
}

# Suggested model shown during onboarding, per provider (override anytime).
SUGGESTED = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "openrouter": "google/gemini-3.5-flash",
    "gemini": "gemini-2.5-flash",
    "groq": "llama-3.3-70b-versatile",
    "mistral": "mistral-large-latest",
    "deepseek": "deepseek-chat",
    "xai": "grok-2-latest",
    "ollama": "llama3",
}


def migrate(cfg: dict) -> dict:
    """Bring an older two-model config (modes.fast/think) up to the single-model
    shape so existing users don't have to re-onboard."""
    if cfg and "model" not in cfg and "modes" in cfg:
        think = cfg.get("modes", {}).get("think", {})
        fast = cfg.get("modes", {}).get("fast", {})
        cfg["model"] = think.get("model") or fast.get("model") or DEFAULTS["model"]
        cfg["allow_web"] = think.get("allow_web", True)
        cfg.pop("modes", None)
    return cfg


def load_config():
    cfg_file = paths.config_path()
    if os.path.exists(cfg_file):
        with open(cfg_file) as f:
            return yaml.safe_load(f)
    return None


def save_config(cfg):
    paths.ensure_dirs()
    with open(paths.config_path(), "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)


# ---- onboarding UI (Rich; light import, no LLM) ------------------------

from rich.console import Console      # noqa: E402
from rich.panel import Panel          # noqa: E402
from rich.prompt import Prompt        # noqa: E402
from rich.text import Text            # noqa: E402

_c = Console()


def _step(n: int, title: str, hint: str = ""):
    _c.print()
    _c.print(f"  [bold cyan]{n}[/][cyan]/4[/]  [bold]{title}[/]")
    if hint:
        _c.print(f"       [dim]{hint}[/]")


def _ok(text):   _c.print(f"       [green]✓[/] {text}")
def _skip(text): _c.print(f"       [dim]○ {text}[/]")
def _warn(text): _c.print(f"       [yellow]⚠[/] {text}")


def _ask(prompt, default=None):
    p = f"       [cyan]›[/] {prompt}"
    return Prompt.ask(p, default=default) if default is not None else Prompt.ask(p)


def _select(prompt, choices, default=None):
    """An arrow-key select menu (questionary); falls back to a typed prompt when
    there's no interactive terminal."""
    import sys
    choices = list(choices)
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            import questionary
            ans = questionary.select(prompt, choices=choices, default=default,
                                     qmark="›", instruction="(↑/↓ · enter)").ask()
            return ans if ans is not None else (default or "")
        except Exception:
            pass
    return _ask(f"{prompt} ({' / '.join(choices)})", default)


def onboard():
    _c.print()
    _c.print(Panel("[bold]Resume Agent[/]  ·  setup\n"
                   "[dim]One model for everything. Your data stays on this machine.[/]",
                   border_style="cyan", padding=(1, 3), expand=False))

    # 1 — provider & model
    _step(1, "Model provider", "↑/↓ to choose — or 'other' for any LiteLLM provider")
    _OTHER = "other…"
    choice = _select("provider", PROVIDERS + [_OTHER], default="anthropic")
    provider = (_ask("provider name (e.g. cohere, bedrock)", "") if choice == _OTHER
                else choice).strip().lower()
    sug = SUGGESTED.get(provider)
    model = (_ask("model", sug) if sug
             else _ask("model (LiteLLM name, e.g. gpt-4o or gemini-2.5-pro)"))

    # 2 — API key
    _step(2, "API key", "kept in a private .env (chmod 600), never in config.yaml")
    env_var = ENV_VAR.get(provider)
    if env_var is None:            # unknown provider — ask which env var holds its key
        env_var = _ask("env var for the API key (blank if none)", "").strip()
    if env_var:
        if os.environ.get(env_var):
            _ok(f"{env_var} is already set in your environment")
        key = _ask(f"paste {env_var} (blank to set later)", "")
        if key:
            write_env_var(env_var, key)
            _ok(f"saved to {paths.env_path()}")
        else:
            _skip(f"skipped — export {env_var} before running")
        key_present = bool(key or os.environ.get(env_var))
    else:
        _skip("no API key needed for this provider")
        key_present = True

    cfg = {"provider": provider, "model": model, "allow_web": True}
    _maybe_setup_search(cfg)
    save_config(cfg)
    _maybe_bootstrap(cfg, key_present=key_present)
    _summary(cfg)
    return cfg


def _summary(cfg):
    body = Text()
    body.append("You're all set.\n\n", style="bold")
    for k in ("provider", "model"):
        body.append(f"  {k:9}", style="dim"); body.append(f"{cfg.get(k, '')}\n")
    body.append(f"  {'web':9}", style="dim")
    body.append("on\n" if cfg.get("allow_web") else "off\n")
    body.append("\n  Start any time:  ", style="dim")
    body.append("resume", style="bold cyan")
    _c.print()
    _c.print(Panel(body, title="[green]✓ done[/]", title_align="left",
                   border_style="green", padding=(1, 3), expand=False))


def _maybe_setup_search(cfg):
    """Optionally enable an explicit web-search tool for think mode. Provider name
    → config.yaml; the API key → git-ignored .env (never config.yaml)."""
    from .search import SEARCH_PROVIDERS, SEARCH_ENV_VAR
    _step(3, "Web research (optional)",
          "an inspectable search tool so `think` can research the target")
    choice = _select("search provider", list(SEARCH_PROVIDERS) + ["none"],
                     default="none").lower()
    if choice not in SEARCH_PROVIDERS:
        _skip("no search tool — think uses the model's own knowledge")
        cfg["search"] = {"provider": "none"}
        return
    env_var = SEARCH_ENV_VAR[choice]
    if os.environ.get(env_var):
        _ok(f"{env_var} is already set in your environment")
    skey = _ask(f"paste {env_var} (blank to set later)", "")
    if skey:
        write_env_var(env_var, skey)
        _ok(f"saved {env_var}")
    else:
        _skip(f"skipped — export {env_var} before using it")
    cfg["search"] = {"provider": choice, "max_results": 3}


def _maybe_bootstrap(cfg, key_present: bool):
    """Offer to build the career wiki from an existing resume during onboarding."""
    from . import store
    if not store.raw_is_empty():
        return  # already have sources; don't re-bootstrap
    _step(4, "Import your resume (optional)",
          "build your career knowledge base from an existing resume (PDF, DOCX…)")
    path = _ask("path to your resume (blank to do it later)", "")
    if not path:
        _skip("skipped — run later with:  resume bootstrap <your-resume>")
        return
    path = os.path.expanduser(path.strip())
    if not os.path.exists(path):
        _warn(f"file not found: {path} — run later with: resume bootstrap <file>")
        return
    if not key_present:
        _warn(f"no API key yet, so it can't compile — run later: resume bootstrap {path}")
        return
    from .extract import extract_to_markdown
    from . import ingest
    from .providers import make_provider
    try:
        text, method = extract_to_markdown(path)
    except Exception as e:
        _warn(f"extraction failed ({e}) — run later: resume bootstrap {path}")
        return
    if not text.strip():
        _warn("couldn't extract text (scanned PDF?) — needs OCR; skipping")
        return
    with _c.status(f"[bold]Compiling your career wiki with {cfg['model']}…[/]", spinner="dots"):
        try:
            res = ingest.bootstrap_from_text(make_provider(cfg["provider"]),
                                             model=cfg["model"], resume_text=text)
        except Exception as e:
            _warn(f"compile failed ({e}) — you can retry: resume recompile")
            return
    if res["ok"]:
        _ok("career wiki compiled — every fact traces to your resume")
    else:
        _warn("compiled with grounding flags to review:")
        for p in res["problems"][:5]:
            _c.print(f"         [dim]- {p}[/]")


def ensure_config():
    load_dotenv()
    cfg = load_config()
    if cfg is None:
        cfg = onboard()
    needs_migrate = "modes" in (cfg or {})
    cfg = migrate(cfg)
    if needs_migrate:
        save_config(cfg)   # rewrite the file once in the new single-model shape
    return cfg
