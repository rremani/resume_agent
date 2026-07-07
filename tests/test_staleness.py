"""Tests for raw/-edit detection and wiki staleness (Task 3).

A manifest records the sha256 of each raw/ file the wiki was last compiled
from. If a raw file is hand-edited, added, or removed, the wiki is stale until
the next compile rewrites the manifest.
"""
import os
import sys
import time

import pytest

CORE_PARENT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, CORE_PARENT)
from core import store  # noqa: E402


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("RESUME_AGENT_HOME", str(tmp_path))
    return tmp_path


def test_no_manifest_means_stale():
    store.add_raw("note", "first", "content")
    assert store.read_manifest() is None
    assert store.wiki_is_stale() is True


def test_fresh_after_write_manifest():
    store.add_raw("note", "first", "content")
    store.write_manifest()
    assert store.read_manifest() is not None
    assert store.wiki_is_stale() is False


def test_editing_a_raw_file_makes_wiki_stale():
    p = store.add_raw("note", "first", "original content")
    store.write_manifest()
    assert store.wiki_is_stale() is False

    with open(p, "a") as f:           # hand-edit the raw source
        f.write("\nsneaky extra line\n")

    assert store.wiki_is_stale() is True
    changes = store.raw_changes()
    assert os.path.basename(p) in changes["modified"]


def test_recompiling_clears_the_warning():
    p = store.add_raw("note", "first", "original")
    store.write_manifest()
    with open(p, "a") as f:
        f.write("\nedit\n")
    assert store.wiki_is_stale() is True

    store.write_manifest()            # what recompile does at the end
    assert store.wiki_is_stale() is False


def test_added_and_removed_files_detected():
    p1 = store.add_raw("note", "one", "a")
    store.write_manifest()
    p2 = store.add_raw("note", "two", "b")        # added after manifest
    assert store.wiki_is_stale() is True
    assert os.path.basename(p2) in store.raw_changes()["added"]

    os.remove(p1)                                  # removed after manifest
    assert os.path.basename(p1) in store.raw_changes()["removed"]


def test_touch_without_content_change_is_not_stale():
    p = store.add_raw("note", "first", "stable content")
    store.write_manifest()
    # bump mtime but keep content identical — sha256 is authoritative
    future = time.time() + 1000
    os.utime(p, (future, future))
    assert store.wiki_is_stale() is False


def test_fingerprint_uses_sha256_and_mtime():
    store.add_raw("note", "first", "content")
    fp = store.raw_fingerprint()
    assert len(fp) == 1
    entry = next(iter(fp.values()))
    assert "sha256" in entry and "mtime" in entry
    assert len(entry["sha256"]) == 64


# ---- CLI wiring: the warning the user actually sees ---------------------

def test_cli_check_staleness_warns_then_clears(capsys):
    import cli
    cfg = {"modes": {"think": {"model": "m"}}}

    p = store.add_raw("note", "first", "content")
    store.write_manifest()

    # fresh → no warning, no provider call (auto=False, provider=None is safe)
    cli._check_staleness(cfg, None, auto=False)
    assert "changed" not in capsys.readouterr().out

    with open(p, "a") as f:
        f.write("\nhand edit\n")

    cli._check_staleness(cfg, None, auto=False)
    out = capsys.readouterr().out
    assert "raw/ changed" in out
    assert "recompile" in out

    store.write_manifest()                      # recompile clears it
    cli._check_staleness(cfg, None, auto=False)
    assert "changed" not in capsys.readouterr().out
