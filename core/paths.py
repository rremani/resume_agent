"""
Per-user data-directory resolution.

The single source of truth for WHERE the app keeps its data: the immutable
raw/ sources, the compiled career/ wiki, generated output/, plus config.yaml
and the secret .env. Everything else imports these helpers instead of deriving
paths from __file__ — so an installed (`pipx`) or frozen (PyInstaller) build
never tries to write inside its own read-only site-packages/bundle.

Resolution (read fresh on every call, so a frozen binary or a test can
relocate the home via the environment):
  1. $RESUME_AGENT_HOME  if set  (also expands ~)
  2. ~/.resume-agent             (the installed default)

Create the home on first use with `ensure_dirs()`.
"""
from __future__ import annotations
import os

ENV_HOME = "RESUME_AGENT_HOME"
DEFAULT_HOME = "~/.resume-agent"


def home() -> str:
    """Absolute path to the user's resume-agent data home."""
    override = os.environ.get(ENV_HOME)
    return os.path.expanduser(override if override else DEFAULT_HOME)


def raw_dir() -> str:
    return os.path.join(home(), "raw")


def career_dir() -> str:
    return os.path.join(home(), "career")


def roles_dir() -> str:
    return os.path.join(career_dir(), "roles")


def projects_dir() -> str:
    return os.path.join(career_dir(), "projects")


def output_dir() -> str:
    return os.path.join(home(), "output")


def manifest_path() -> str:
    """Record of what raw/ the career wiki was last compiled from."""
    return os.path.join(career_dir(), ".manifest.json")


def config_path() -> str:
    return os.path.join(home(), "config.yaml")


def env_path() -> str:
    return os.path.join(home(), ".env")


def ensure_dirs() -> None:
    """Create the data home and all its subdirectories if missing."""
    for d in (home(), raw_dir(), career_dir(), roles_dir(),
              projects_dir(), output_dir()):
        os.makedirs(d, exist_ok=True)
