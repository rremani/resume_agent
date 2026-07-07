"""Tests for wiki retrieval helpers (store.list_pages / search_wiki / read_page).

These back the think-mode research agent's LIST / FIND / READ tools: it browses
its own career wiki instead of being handed the whole blob.
"""
import os
import sys

import pytest

CORE_PARENT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, CORE_PARENT)
from core import store  # noqa: E402


@pytest.fixture(autouse=True)
def wiki(monkeypatch, tmp_path):
    monkeypatch.setenv("RESUME_AGENT_HOME", str(tmp_path))
    store.write_career_file("profile.md",
                            {"name": "Ada Lovelace", "title": "Data Scientist",
                             "contact": {"email": "a@b.com"}, "education": []},
                            "Seasoned engineer.")
    store.write_career_file("skills.md", {"skills": [{"category": "ML", "items": ["XGBoost"]}]}, "")
    store.write_career_file("roles/ensuredit.md",
                            {"role": "Data Lead", "company": "Ensuredit Technologies",
                             "start": "02/2021", "end": "02/2023", "location": "Gurgaon",
                             "bullets": [{"text": "Fine-tuned LayoutLMv2 for insurance document extraction, 90% F1."}]},
                            "InsureTech underwriting automation.")
    store.write_career_file("roles/saal.md",
                            {"role": "Data Scientist", "company": "SAAL.AI",
                             "start": "03/2023", "end": "Present", "location": "Abu Dhabi",
                             "bullets": [{"text": "Built an agentic chatbot with DSPy and LLM orchestration."}]},
                            "EdTech analytics platform.")
    store.write_career_file("projects/fraud.md",
                            {"name": "Fraud Detection", "tags": ["fraud"],
                             "bullets": [{"text": "Gradient boosting model for transaction fraud."}]},
                            "Consulting project on fraud detection.")
    return tmp_path


def test_list_pages_indexes_every_page():
    pages = store.list_pages()
    kinds = sorted(p["kind"] for p in pages)
    assert kinds == ["profile", "project", "role", "role", "skills"]
    slugs = {p["slug"] for p in pages}
    assert {"profile", "skills", "roles/ensuredit", "roles/saal", "projects/fraud"} == slugs
    # role title surfaces the company
    ensure = next(p for p in pages if p["slug"] == "roles/ensuredit")
    assert ensure["title"] == "Ensuredit Technologies"


def test_search_ranks_relevant_page_first():
    results = store.search_wiki("insurance document extraction LayoutLMv2")
    assert results, "expected at least one hit"
    assert results[0]["slug"] == "roles/ensuredit"
    # an unrelated page should not outrank it
    saal = next((r for r in results if r["slug"] == "roles/saal"), None)
    if saal:
        assert results[0]["score"] >= saal["score"]


def test_search_finds_projects():
    results = store.search_wiki("fraud transaction")
    assert results[0]["slug"] == "projects/fraud"
    assert "snippet" in results[0] and results[0]["snippet"]


def test_search_no_match_returns_empty():
    assert store.search_wiki("quantum chromodynamics tokamak") == []


def test_search_respects_limit():
    results = store.search_wiki("a e i o u the and", limit=2)
    assert len(results) <= 2


def test_read_page_returns_content_and_none_for_unknown():
    txt = store.read_page("roles/ensuredit")
    assert txt and "Ensuredit Technologies" in txt
    assert store.read_page("roles/ensuredit.md"), "should tolerate trailing .md"
    assert store.read_page("roles/does-not-exist") is None


def test_read_page_blocks_path_traversal():
    assert store.read_page("../../etc/passwd") is None
