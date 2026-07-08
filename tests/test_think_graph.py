"""Tests for the LangGraph editor workflow (core/think_graph).

The full graph needs a live LLM, so these cover the deterministic pieces: the
routers, the draft step (via a stub model), the preview, the model builder, and
the dash-tolerant grounding.
"""
import os
import sys

import pytest
from langgraph.graph import END

CORE_PARENT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, CORE_PARENT)
from core import think_graph as tg, agent as core_agent  # noqa: E402


# ---- stub model: with_structured_output(schema).invoke(...) -> fixed obj ----

class _Structured:
    def __init__(self, obj):
        self.obj = obj
        self.calls = []

    def invoke(self, prompt):
        self.calls.append(prompt)
        return self.obj


class StubModel:
    def __init__(self, by_schema):
        self.by_schema = by_schema
        self.structured = {}

    def with_structured_output(self, schema):
        s = _Structured(self.by_schema[schema])
        self.structured[schema] = s
        return s


# ---- routers ------------------------------------------------------------

def test_route_understand():
    assert tg._route_understand({"aborted": True}) == END
    assert tg._route_understand({"aborted": False}) == "blueprint"


def test_route_improve():
    assert tg._route_improve({"feedback": "__abort__"}) == END
    assert tg._route_improve({"feedback": "done"}) == "finalize"
    # feedback applies a surgical edit — it does NOT re-run blueprint/fill.
    assert tg._route_improve({"feedback": "punchier", "rounds": 1}) == "edit"
    assert tg._route_improve({"feedback": "more", "rounds": tg.MAX_ROUNDS}) == "finalize"


# ---- blueprint step (stub model) ---------------------------------------

def test_blueprint_uses_inventory_and_target():
    bp = tg.Blueprint(title="Data Architect",
                      themes=[tg.Theme(name="GenAI", why="fit", priority=1)],
                      lead_skills=["LLM"])
    model = StubModel({tg.Blueprint: bp})
    out = tg.blueprint_resume(model, target="Data Architect, GenAI",
                              inventory="ROLES:\n  - DS — Acme")
    assert out.title == "Data Architect"
    sent = str(model.structured[tg.Blueprint].calls[0])
    assert "Data Architect, GenAI" in sent and "ROLES:" in sent


# ---- fill step (stub model) --------------------------------------------

def test_fill_passes_blueprint_and_evidence():
    want = tg.Resume(name="Ada", experience=[tg.Job(role="Eng", company="Acme")])
    model = StubModel({tg.Resume: want})
    bp = {"title": "T", "themes": [{"name": "GenAI", "why": "y", "priority": 1}]}
    out = tg.fill_resume(model, blueprint=bp, evidence="wiki bundle text")
    assert out.name == "Ada"
    sent = str(model.structured[tg.Resume].calls[0])
    assert "wiki bundle text" in sent and "GenAI" in sent


def test_fill_system_forbids_fabricating_empty_themes():
    # The structural fabrication guard: a theme with no evidence is dropped.
    assert "DROPPED" in tg.FILL_SYSTEM and "never fabricated" in tg.FILL_SYSTEM


def test_fill_system_guides_concise_relevance_based_length():
    # Length is LLM-decided by relevance — no hardcoded per-role bullet counts.
    assert "skimmable" in tg.FILL_SYSTEM and "relevan" in tg.FILL_SYSTEM


# ---- edit step (stub model) --------------------------------------------

def test_edit_applies_one_change_to_current():
    model = StubModel({tg.Resume: tg.Resume(name="Ada")})
    tg.draft_resume(model, target="t", evidence="ev",
                    feedback="make it punchier", current={"name": "Ada"})
    sent = str(model.structured[tg.Resume].calls[0])
    assert "CURRENT DRAFT" in sent and "make it punchier" in sent


def test_preview_lists_roles_and_bullet_counts():
    draft = {"name": "Ada", "summary": "hi",
             "experience": [{"role": "Eng", "company": "Acme",
                             "start": "01/2020", "end": "Present",
                             "bullets": [{"text": "x"}, {"text": "y"}]}],
             "skills": [{"category": "ML"}]}
    p = tg.preview(draft)
    assert "Ada" in p and "Eng — Acme" in p and "[2 bullets]" in p and "ML" in p


def test_preview_shows_blueprint_strategy_line():
    draft = {"name": "Ada", "summary": "", "experience": [], "skills": []}
    bp = {"title": "Data Architect",
          "themes": [{"name": "GenAI"}, {"name": "MLOps"}]}
    p = tg.preview(draft, bp)
    assert "Tailoring toward: Data Architect" in p and "GenAI" in p and "MLOps" in p


# ---- grounding: dash-retitle is not a false flag -----------------------

def test_grounding_tolerates_dash_retitle():
    store = {"experience": [{"company": "Vogo", "role": "Senior Engineer - Machine Learning"}]}
    gen = {"experience": [{"company": "Vogo", "role": "Senior Engineer — Machine Learning"}]}
    ok, problems = core_agent.validate(gen, store)
    assert ok and not problems


def test_grounding_allows_numbers_from_project_evidence():
    """A metric the agent legitimately pulled from a project page (present in the
    bundle but NOT in the thin resume YAML) must not be flagged as ungrounded."""
    store = {"experience": [{"company": "IMKAN", "role": "Data Scientist"}]}  # thin: no 300
    gen = {"experience": [{"company": "IMKAN", "role": "Data Scientist",
                           "bullets": [{"text": "pricing model covering ~300 units"}]}]}
    # Without evidence → false positive (documents the old bug).
    ok0, _ = core_agent.validate(gen, store)
    assert not ok0
    # With the bundle the agent read → grounded.
    ok1, problems = core_agent.validate(gen, store,
                                        evidence="Model covers ~300 units / price points.")
    assert ok1 and not problems


# ---- model builder + schema --------------------------------------------

def test_build_model_selects_provider(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    assert type(tg._build_model({"provider": "anthropic"}, "claude-sonnet-4-6")).__name__ == "ChatAnthropic"
    assert type(tg._build_model({"provider": "openrouter"}, "google/gemini-3.5-flash")).__name__ == "ChatOpenAI"
    with pytest.raises(ValueError):
        tg._build_model({"provider": "nope"}, "x")


def test_resume_schema_round_trips():
    r = tg.Resume(name="Ada", experience=[
        tg.Job(role="Eng", company="Acme", bullets=[tg.Bullet(text="did X")])])
    assert r.model_dump()["experience"][0]["bullets"][0]["text"] == "did X"


def test_bullet_schema_has_no_tags():
    # tags are unused when tailoring; keeping them out of the schema shrinks output
    assert "tags" not in tg.Bullet.model_fields
    assert "tags" not in tg.SkillGroup.model_fields
