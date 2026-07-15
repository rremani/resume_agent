"""The 'no wiki yet' guard offers to build one inline instead of dead-ending.

Covers cli._ensure_wiki: pass-through when a wiki exists, an inline bootstrap
offer when there are no sources, and the recompile branch (not bootstrap) when
raw sources exist but were never compiled.
"""
import os
import sys

import pytest

CORE_PARENT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, CORE_PARENT)
import cli  # noqa: E402
from core import config as _config, store  # noqa: E402

CFG = {"provider": "anthropic", "modes": {"think": {"model": "x"}}}


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("RESUME_AGENT_HOME", str(tmp_path))
    return tmp_path


def test_passes_through_when_wiki_present():
    store.write_career_file("profile.md", {"name": "A"}, "hi")
    assert cli._ensure_wiki(CFG) is True


def test_offers_inline_bootstrap_when_no_sources(monkeypatch):
    calls = {}
    monkeypatch.setattr(_config, "_maybe_bootstrap",
                        lambda cfg, key_present: calls.setdefault("offered", True))
    # empty home → career empty AND raw empty → inline bootstrap offered.
    # The stub builds nothing, so the wiki is still empty → returns False.
    assert cli._ensure_wiki(CFG) is False
    assert calls.get("offered")


def test_recompile_branch_when_raw_exists_not_bootstrap(monkeypatch):
    store.add_raw("note", "first", "content")          # raw non-empty, career empty
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    calls = {}
    monkeypatch.setattr(_config, "_maybe_bootstrap",
                        lambda *a, **k: calls.setdefault("offered", True))
    # raw already has sources → this is a recompile situation, NOT a bootstrap.
    assert cli._ensure_wiki(CFG) is False
    assert "offered" not in calls                       # bootstrap must NOT be offered
