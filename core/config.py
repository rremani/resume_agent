"""
Config + onboarding.

First run walks the user through provider, models per mode, API key, and an
optional resume to bootstrap from. Non-secret settings persist to config.yaml.
The API key is written to a git-ignored .env (never to config.yaml), and loaded
automatically on every run. Re-runnable via `python cli.py onboard`.
"""

from __future__ import annotations
import os
import yaml

BASE = os.path.dirname(os.path.dirname(__file__))
CONFIG_PATH = os.path.join(BASE, "config.yaml")
ENV_PATH = os.path.join(BASE, ".env")

ENV_VAR = {"anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"}


def load_dotenv():
    """Load KEY=VALUE lines from .env into os.environ (without overwriting
    anything already set in the real environment). Called at startup."""
    if not os.path.exists(ENV_PATH):
        return
    for line in open(ENV_PATH):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def write_env_var(name: str, value: str):
    """Upsert one KEY=VALUE into .env, preserving other lines. Chmod 600."""
    lines = []
    found = False
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH):
            if line.strip().startswith(name + "="):
                lines.append(f"{name}={value}\n")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{name}={value}\n")
    with open(ENV_PATH, "w") as f:
        f.writelines(lines)
    try:
        os.chmod(ENV_PATH, 0o600)  # owner-only
    except OSError:
        pass
    os.environ[name] = value

DEFAULTS = {
    "provider": "anthropic",
    "modes": {
        "fast": {
            "model": "claude-haiku-4-5-20251001",
            "allow_web": False,
            "conversational": False,
        },
        "think": {
            "model": "claude-opus-4-8",
            "allow_web": True,
            "conversational": True,
        },
    },
}

# Suggested defaults shown during onboarding, per provider.
SUGGESTED = {
    "anthropic": {
        "fast": "claude-haiku-4-5-20251001",
        "think": "claude-opus-4-8",
    },
    "openrouter": {
        "fast": "anthropic/claude-haiku-4.5",
        "think": "anthropic/claude-opus-4.8",
    },
}


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    return None


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
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
    print("\nNow pick models for each mode.")
    print("  fast  = cheaper model, single-shot, no web search")
    print("  think = stronger model, conversational, web search enabled\n")
    fast_model = _ask("Fast-mode model", sug["fast"])
    think_model = _ask("Think-mode model", sug["think"])

    # --- API key → .env (never config.yaml) ---
    env_var = ENV_VAR[provider]
    print(f"\nAPI key (stored in a private .env file, not in config.yaml).")
    if os.environ.get(env_var):
        print(f"  {env_var} is already set in your environment.")
    key = _ask(f"Paste your {env_var} (or leave blank to set it later)", "")
    if key:
        write_env_var(env_var, key)
        print(f"  ✓ Saved to {ENV_PATH} (owner-only). Loaded automatically from now on.")
    else:
        print(f"  Skipped. Export {env_var} before running, or re-run onboarding.")

    cfg = {
        "provider": provider,
        "modes": {
            "fast": {"model": fast_model, "allow_web": False, "conversational": False},
            "think": {"model": think_model, "allow_web": True, "conversational": True},
        },
    }
    save_config(cfg)
    print(f"\n✓ Saved config to {CONFIG_PATH}")

    # --- optional: bootstrap from an existing resume right now ---
    _maybe_bootstrap(cfg, key_present=bool(key or os.environ.get(env_var)))

    print("\nSetup complete. Generate any time with:")
    print('  python cli.py fast  "GenAI role at a bank, emphasize LLM + risk"')
    print("  python cli.py think --file jd.txt\n")
    return cfg


def _maybe_bootstrap(cfg, key_present: bool):
    """Offer to build the career wiki from an existing resume during onboarding."""
    from . import store
    if not store.raw_is_empty():
        return  # already have sources; don't re-bootstrap
    print("\nBuild your career wiki from an existing resume now? (PDF, DOCX, etc.)")
    path = _ask("Path to your resume (or leave blank to do it later)", "")
    if not path:
        print("  Skipped. Run later with: python cli.py bootstrap <your-resume>")
        return
    path = os.path.expanduser(path.strip())
    if not os.path.exists(path):
        print(f"  File not found: {path}. Run later with: python cli.py bootstrap <file>")
        return
    if not key_present:
        print("  No API key available, so the wiki can't be compiled yet.")
        print(f"  Your resume path is noted; run: python cli.py bootstrap {path}")
        return
    # Do the actual bootstrap (extract → raw → compile)
    from .extract import extract_to_markdown
    from . import ingest
    from .providers import make_provider
    try:
        text, method = extract_to_markdown(path)
    except Exception as e:
        print(f"  Extraction failed ({e}). Run later with: python cli.py bootstrap {path}")
        return
    if not text.strip():
        print("  Could not extract text (scanned PDF?). Needs OCR; skipping for now.")
        return
    print(f"  Extracted via {method}; compiling wiki with {cfg['modes']['think']['model']}…")
    try:
        provider = make_provider(cfg["provider"])
        res = ingest.bootstrap_from_text(provider, model=cfg["modes"]["think"]["model"],
                                         resume_text=text)
        if res["ok"]:
            print("  ✓ Career wiki compiled — every fact traces to your resume.")
        else:
            print("  ⚠ Wiki compiled with grounding flags to review:")
            for p in res["problems"][:5]:
                print("     -", p)
    except Exception as e:
        print(f"  Compile failed ({e}). You can retry: python cli.py recompile")


def ensure_config():
    load_dotenv()
    cfg = load_config()
    if cfg is None:
        cfg = onboard()
    return cfg
