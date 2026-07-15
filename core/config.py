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

ENV_VAR = {"anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"}


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

# Suggested model shown during onboarding, per provider.
SUGGESTED = {
    "anthropic": "claude-sonnet-4-6",
    "openrouter": "google/gemini-3.5-flash",
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


def _ask(prompt, default=None):
    try:
        from rich.prompt import Prompt
        return Prompt.ask(prompt, default=default) if default else Prompt.ask(prompt)
    except Exception:
        suffix = f" [{default}]" if default else ""
        val = input(f"{prompt}{suffix}: ").strip()
        return val or (default or "")


def onboard():
    print("\n=== Resume Agent — onboarding ===\n")
    print("Choose your model provider.")
    provider = ""
    while provider not in ("anthropic", "openrouter"):
        provider = _ask("Provider (anthropic / openrouter)", "anthropic").lower()

    sug = SUGGESTED[provider]
    print("\nPick the model to use (one model for everything).")
    print("  fast = one-shot · think = conversational — both use this model.\n")
    model = _ask("Model", sug)

    # --- API key → .env (never config.yaml) ---
    env_var = ENV_VAR[provider]
    print(f"\nAPI key (stored in a private .env file, not in config.yaml).")
    if os.environ.get(env_var):
        print(f"  {env_var} is already set in your environment.")
    key = _ask(f"Paste your {env_var} (or leave blank to set it later)", "")
    if key:
        write_env_var(env_var, key)
        print(f"  ✓ Saved to {paths.env_path()} (owner-only). Loaded automatically from now on.")
    else:
        print(f"  Skipped. Export {env_var} before running, or re-run onboarding.")

    cfg = {
        "provider": provider,
        "model": model,
        "allow_web": True,
    }
    _maybe_setup_search(cfg)
    save_config(cfg)
    print(f"\n✓ Saved config to {paths.config_path()}")

    # --- optional: bootstrap from an existing resume right now ---
    _maybe_bootstrap(cfg, key_present=bool(key or os.environ.get(env_var)))

    print("\nSetup complete. Generate any time with:")
    print('  resume fast  "GenAI role at a bank, emphasize LLM + risk"')
    print("  resume think --file jd.txt\n")
    return cfg


def _maybe_setup_search(cfg):
    """Optionally enable an explicit web-search tool for think mode.

    Provider name → config.yaml; the API key → git-ignored .env (never
    config.yaml), mirroring the model-key flow above. Default 'none' keeps
    think mode on the provider's built-in web search only."""
    from .search import SEARCH_PROVIDERS, SEARCH_ENV_VAR
    print("\nOptional: an explicit, inspectable web-search tool for "
          "[think] mode\n  (research a target company, pull a JD, verify a fact "
          "before tailoring).")
    print(f"  Providers: {', '.join(SEARCH_PROVIDERS)} — or 'none' to use only "
          "the model provider's built-in web search.")
    choice = _ask("Search provider (tavily / exa / brave / none)", "none").lower()
    if choice not in SEARCH_PROVIDERS:
        cfg["search"] = {"provider": "none"}
        return
    env_var = SEARCH_ENV_VAR[choice]
    if os.environ.get(env_var):
        print(f"  {env_var} is already set in your environment.")
    skey = _ask(f"Paste your {env_var} (or leave blank to set it later)", "")
    if skey:
        write_env_var(env_var, skey)
        print(f"  ✓ Saved {env_var} to {paths.env_path()} (owner-only).")
    else:
        print(f"  Skipped — export {env_var} or re-run onboarding before using it.")
    cfg["search"] = {"provider": choice, "max_results": 3}


def _maybe_bootstrap(cfg, key_present: bool):
    """Offer to build the career wiki from an existing resume during onboarding."""
    from . import store
    if not store.raw_is_empty():
        return  # already have sources; don't re-bootstrap
    print("\nBuild your career wiki from an existing resume now? (PDF, DOCX, etc.)")
    path = _ask("Path to your resume (or leave blank to do it later)", "")
    if not path:
        print("  Skipped. Run later with: resume bootstrap <your-resume>")
        return
    path = os.path.expanduser(path.strip())
    if not os.path.exists(path):
        print(f"  File not found: {path}. Run later with: resume bootstrap <file>")
        return
    if not key_present:
        print("  No API key available, so the wiki can't be compiled yet.")
        print(f"  Your resume path is noted; run: resume bootstrap {path}")
        return
    # Do the actual bootstrap (extract → raw → compile)
    from .extract import extract_to_markdown
    from . import ingest
    from .providers import make_provider
    try:
        text, method = extract_to_markdown(path)
    except Exception as e:
        print(f"  Extraction failed ({e}). Run later with: resume bootstrap {path}")
        return
    if not text.strip():
        print("  Could not extract text (scanned PDF?). Needs OCR; skipping for now.")
        return
    print(f"  Extracted via {method}; compiling wiki with {cfg['model']}…")
    try:
        provider = make_provider(cfg["provider"])
        res = ingest.bootstrap_from_text(provider, model=cfg["model"],
                                         resume_text=text)
        if res["ok"]:
            print("  ✓ Career wiki compiled — every fact traces to your resume.")
        else:
            print("  ⚠ Wiki compiled with grounding flags to review:")
            for p in res["problems"][:5]:
                print("     -", p)
    except Exception as e:
        print(f"  Compile failed ({e}). You can retry: resume recompile")


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
