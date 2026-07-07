"""Compile is a CLEAN rebuild, not an additive overlay.

The LLM can emit a different slug for the same role/project across runs, so a
recompile must sweep stale per-slug pages — otherwise the wiki accumulates
duplicate pages (the real cause of the doubled IMKAN project).
"""
import os
import sys

import pytest

CORE_PARENT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, CORE_PARENT)
from core import compiler, paths, store  # noqa: E402


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("RESUME_AGENT_HOME", str(tmp_path))
    return tmp_path


def _project(slug, name):
    return {"profile": {"name": "A"}, "skills": [],
            "roles": [], "projects": [{"slug": slug, "name": name, "bullets": []}]}


def test_recompile_sweeps_stale_slug():
    # First compile writes the project under one slug.
    compiler._write_pages(_project("imkan-dynamic-pricing", "IMKAN Pricing"))
    # Second compile emits the SAME project under a different slug.
    compiler._write_pages(_project("dynamic-pricing-imkan", "IMKAN Pricing"))

    pages = os.listdir(paths.projects_dir())
    assert pages == ["dynamic-pricing-imkan.md"], pages   # no orphan left behind


def test_clear_leaves_fixed_name_pages_untouched():
    # profile.md / skills.md have fixed names and must survive the sweep.
    compiler._write_pages(_project("p1", "One"))
    assert os.path.exists(os.path.join(paths.career_dir(), "profile.md"))
    assert os.path.exists(os.path.join(paths.career_dir(), "skills.md"))
    compiler._write_pages(_project("p2", "Two"))
    assert os.path.exists(os.path.join(paths.career_dir(), "profile.md"))
    assert os.path.exists(os.path.join(paths.career_dir(), "skills.md"))
