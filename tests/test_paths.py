"""Tests for per-user data-directory resolution (core/paths.py).

An installed `resume` command must NOT read/write data inside its own
site-packages/frozen bundle. All user data lives under a per-user home,
defaulting to ~/.resume-agent, overridable via RESUME_AGENT_HOME.
"""
import os
import sys

CORE_PARENT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, CORE_PARENT)
from core import paths  # noqa: E402


def test_default_home_is_dot_resume_agent(monkeypatch):
    monkeypatch.delenv("RESUME_AGENT_HOME", raising=False)
    assert paths.home() == os.path.expanduser("~/.resume-agent")


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("RESUME_AGENT_HOME", str(tmp_path / "custom"))
    assert paths.home() == str(tmp_path / "custom")


def test_env_override_expands_user(monkeypatch):
    monkeypatch.setenv("RESUME_AGENT_HOME", "~/somewhere")
    assert paths.home() == os.path.expanduser("~/somewhere")


def test_derived_paths_live_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("RESUME_AGENT_HOME", str(tmp_path))
    h = str(tmp_path)
    assert paths.raw_dir() == os.path.join(h, "raw")
    assert paths.career_dir() == os.path.join(h, "career")
    assert paths.roles_dir() == os.path.join(h, "career", "roles")
    assert paths.projects_dir() == os.path.join(h, "career", "projects")
    assert paths.output_dir() == os.path.join(h, "output")
    assert paths.config_path() == os.path.join(h, "config.yaml")
    assert paths.env_path() == os.path.join(h, ".env")


def test_resolution_is_dynamic_not_cached(monkeypatch, tmp_path):
    """Reads env at call time, so a frozen binary or test can relocate it."""
    monkeypatch.setenv("RESUME_AGENT_HOME", str(tmp_path / "a"))
    first = paths.home()
    monkeypatch.setenv("RESUME_AGENT_HOME", str(tmp_path / "b"))
    second = paths.home()
    assert first != second
