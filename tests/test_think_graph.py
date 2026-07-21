"""Tests for the JD-driven tailoring workflow (core/think_graph).

The full graph needs a live LLM, so these cover the deterministic pieces plus the
LLM steps with `core.llm` stubbed: routers, assess/fill/edit, gap helpers, sections,
identity-lock, provenance grounding, greeting guards, and end-to-end flows.
"""
import os
import sys

from langgraph.graph import END

CORE_PARENT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, CORE_PARENT)
from core import think_graph as tg, agent as core_agent  # noqa: E402


# ---- stub the unified model layer (core.llm) ---------------------------

class _LLMStub:
    """structured() returns a fixed object per schema (recording the prompt);
    chat() returns a fixed string."""
    def __init__(self, by_schema, chat_text):
        self.by_schema = by_schema
        self.chat_text = chat_text
        self.calls = []                       # (system, user, schema)

    def structured(self, system, user, schema, *, model, max_tokens=8192):
        self.calls.append((system, user, schema))
        return self.by_schema[schema]

    def chat(self, system, user, *, model, max_tokens=4096):
        return self.chat_text

    def sent_for(self, schema):
        return next(f"{s}\n{u}" for s, u, sc in self.calls if sc is schema)


def _patch_llm(monkeypatch, by_schema=None, chat_text="Hi! What role are you targeting?"):
    stub = _LLMStub(by_schema or {}, chat_text)
    monkeypatch.setattr(tg.llm, "structured", stub.structured)
    monkeypatch.setattr(tg.llm, "chat", stub.chat)
    return stub


# ---- routers ------------------------------------------------------------

def test_route_understand():
    assert tg._route_understand({"aborted": True}) == END
    assert tg._route_understand({"aborted": False}) == "assess"


def test_route_convo():
    assert tg._route_convo({"aborted": True}) == END
    assert tg._route_convo({"aborted": False}) == "fill"


def test_route_refine():
    assert tg._route_refine({"aborted": True}) == END
    assert tg._route_refine({"aborted": False}) == "finalize"


# ---- model string resolution -------------------------------------------

def test_model_string_resolution():
    assert tg._model({"provider": "anthropic", "model": "claude-sonnet-4-6"}) \
        == "anthropic/claude-sonnet-4-6"
    assert tg._model({"provider": "openrouter", "model": "google/gemini-3.5-flash"}) \
        == "openrouter/google/gemini-3.5-flash"
    # any configured provider is prefixed (broadened support)
    assert tg._model({"provider": "gemini", "model": "gemini-2.5-flash"}) == "gemini/gemini-2.5-flash"
    assert tg._model({"provider": "groq", "model": "llama-3.3-70b"}) == "groq/llama-3.3-70b"
    # a full LiteLLM model string passes through untouched
    assert tg._model({"provider": "openrouter", "model": "groq/llama-3.1-70b"}) \
        == "groq/llama-3.1-70b"


# ---- assess step --------------------------------------------------------

def test_assess_reads_target_and_evidence(monkeypatch):
    a = tg.Assessment(role_title="Data Architect",
                      requirements=[tg.Requirement(name="Kubernetes", covered=False)])
    stub = _patch_llm(monkeypatch, {tg.Assessment: a})
    out = tg.assess_jd("m", target="Data Architect, GenAI", evidence="wiki bundle")
    assert out.role_title == "Data Architect"
    sent = stub.sent_for(tg.Assessment)
    assert "Data Architect, GenAI" in sent and "wiki bundle" in sent


def test_gaps_of_filters_uncovered_and_must():
    a = {"requirements": [
        {"name": "K8s", "importance": "must", "covered": False},
        {"name": "RAG", "importance": "must", "covered": True},
        {"name": "Terraform", "importance": "nice", "covered": False}]}
    assert [g["name"] for g in tg.gaps_of(a)] == ["K8s", "Terraform"]
    assert [g["name"] for g in tg.gaps_of(a, must_only=True)] == ["K8s"]


def test_strategy_of_uses_covered_requirements():
    a = {"role_title": "LLM Engineer",
         "requirements": [{"name": "Agentic AI", "covered": True},
                          {"name": "K8s", "covered": False}]}
    s = tg.strategy_of(a)
    assert s["title"] == "LLM Engineer"
    assert [t["name"] for t in s["themes"]] == ["Agentic AI"]   # gaps excluded


# ---- fill step ----------------------------------------------------------

def test_fill_uses_assessment_evidence_and_provided(monkeypatch):
    want = tg.Resume(name="Ada", experience=[tg.Job(role="Eng", company="Acme")])
    stub = _patch_llm(monkeypatch, {tg.Resume: want})
    out = tg.fill_resume("m", assessment={"role_title": "LLM Eng"},
                         evidence="wiki bundle text", provided=["Kubernetes: ran prod clusters"])
    assert out.name == "Ada"
    sent = stub.sent_for(tg.Resume)
    assert "wiki bundle text" in sent and "LLM Eng" in sent and "Kubernetes" in sent


def test_fill_system_encodes_local_optimum_no_fabrication():
    assert "fabricate" in tg.FILL_SYSTEM and "gap stays a gap" in tg.FILL_SYSTEM
    assert "LOCAL OPTIMUM" in tg.FILL_SYSTEM


# ---- sections + scoped edits (the fix for whole-resume data loss) ------

_DRAFT = {"name": "Ada", "summary": "seasoned",
          "experience": [
              {"role": "Eng", "company": "Acme", "start": "01/2020", "end": "Present",
               "bullets": [{"text": "Built X"}, {"text": "Shipped Y"}, {"text": "Scaled Z"}]},
              {"role": "Dev", "company": "Beta", "bullets": [{"text": "Made W"}]}],
          "skills": [{"category": "ML", "items": ["a", "b"]}]}


def test_sections_and_menu():
    secs = tg.sections(_DRAFT)
    assert secs == [("summary",), ("exp", 0), ("exp", 1), ("skills",)]
    menu = tg.section_menu(_DRAFT)
    assert "Summary" in menu and "Eng — Acme  (3 bullets)" in menu and "Skills" in menu


def test_section_view_numbers_within_the_role():
    v = tg.section_view(_DRAFT, ("exp", 0))
    assert "Eng — Acme" in v and "1. Built X" in v and "3. Scaled Z" in v


def test_parse_drops():
    assert tg.parse_drops("drop 4 5 6") == [4, 5, 6]
    assert tg.parse_drops("remove #2") == [2]
    assert tg.parse_drops("tighten 3") is None      # not a delete → LLM handles it


def test_apply_section_edit_drop_is_deterministic_and_local():
    # dropping bullets in role 0 must NOT touch role 1 or any other section.
    out = tg.apply_section_edit(None, _DRAFT, ("exp", 0), "drop 1 3", evidence="ev")
    assert [b["text"] for b in out["experience"][0]["bullets"]] == ["Shipped Y"]
    assert out["experience"][1]["bullets"] == [{"text": "Made W"}]   # untouched
    assert out["skills"] == _DRAFT["skills"]
    assert _DRAFT["experience"][0]["bullets"][0]["text"] == "Built X"  # input not mutated


def test_apply_section_edit_wording_calls_llm_scoped_to_role(monkeypatch):
    stub = _patch_llm(monkeypatch,
                      {tg.Bullets: tg.Bullets(bullets=[tg.Bullet(text="Built X, at scale")])})
    out = tg.apply_section_edit("m", _DRAFT, ("exp", 0), "tighten and add scale", evidence="ev")
    assert [b["text"] for b in out["experience"][0]["bullets"]] == ["Built X, at scale"]
    assert out["experience"][1]["bullets"] == [{"text": "Made W"}]   # other role safe
    sent = stub.sent_for(tg.Bullets)
    assert "Eng — Acme" in sent and "tighten and add scale" in sent


def test_lock_identity_reverts_inflated_title_and_company():
    wiki = {"name": "Ada", "experience": [
        {"role": "Data Scientist", "company": "SAAL.AI", "start": "03/2023", "end": "Present"}]}
    draft = {"name": "Ada X.", "experience": [
        {"role": "Lead Data Scientist (GenAI Lead)", "company": "SAAL.AI",
         "start": "01/2020", "end": "Now", "bullets": [{"text": "b"}]}]}
    out = tg.lock_identity(draft, wiki)
    r = out["experience"][0]
    assert r["role"] == "Data Scientist"                 # inflation reverted
    assert r["start"] == "03/2023" and r["end"] == "Present"
    assert r["bullets"] == [{"text": "b"}]               # bullets untouched
    assert out["name"] == "Ada"                          # name locked to wiki


def test_authorizes_estimates():
    for yes in ["you can fill them as per your understanding ill verify",
                "add metrics wherever you feel are not present",
                "you decide", "fill them in", "estimate it", "reasonable numbers"]:
        assert tg.authorizes_estimates(yes), yes
    for no in ["DSPy insights improved accuracy 12%", "no", "tighten 1", "drop 2"]:
        assert not tg.authorizes_estimates(no), no


def test_fill_locks_role_title_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setenv("RESUME_AGENT_HOME", str(tmp_path))
    _patch_llm(monkeypatch, {
        tg.Assessment: tg.Assessment(role_title="T", requirements=[]),
        tg.Resume: tg.Resume(name="Ada", experience=[tg.Job(
            role="Lead Data Scientist (GenAI Lead)", company="SAAL.AI",
            bullets=[tg.Bullet(text="b")])])})
    monkeypatch.setattr(tg.compiler, "wiki_to_resume_yaml", lambda: {
        "name": "Ada", "skills": [],
        "experience": [{"role": "Data Scientist", "company": "SAAL.AI",
                        "start": "03/2023", "end": "Present"}]})
    monkeypatch.setattr(tg.store, "read_career_bundle", lambda: "wiki")

    captured = {}
    tg.run_think({"provider": "anthropic", "model": "x"},
                 opening="a sufficiently long target brief so understand skips its question",
                 out_stem="t", ask_fn=lambda: "done",
                 say_fn=lambda t: None, notify_fn=lambda t: None,
                 render_fn=lambda data, stem: (captured.update(data=data) or ("d", "p")),
                 allow_web=False)
    assert captured["data"]["experience"][0]["role"] == "Data Scientist"   # not inflated


def test_strategy_text_from_assessment():
    a = {"role_title": "Data Architect",
         "requirements": [{"name": "GenAI", "covered": True},
                          {"name": "MLOps", "covered": True},
                          {"name": "K8s", "covered": False}]}
    t = tg.strategy_text(a)
    assert "Tailoring toward: Data Architect" in t and "GenAI" in t and "MLOps" in t
    assert "K8s" not in t   # gaps aren't part of the strategy


# ---- grounding ----------------------------------------------------------

def test_grounding_tolerates_dash_retitle():
    store = {"experience": [{"company": "Vogo", "role": "Senior Engineer - Machine Learning"}]}
    gen = {"experience": [{"company": "Vogo", "role": "Senior Engineer — Machine Learning"}]}
    ok, problems = core_agent.validate(gen, store)
    assert ok and not problems


def test_grounding_allows_numbers_from_project_evidence():
    store = {"experience": [{"company": "IMKAN", "role": "Data Scientist"}]}  # thin: no 300
    gen = {"experience": [{"company": "IMKAN", "role": "Data Scientist",
                           "bullets": [{"text": "pricing model covering ~300 units"}]}]}
    ok0, _ = core_agent.validate(gen, store)
    assert not ok0
    ok1, problems = core_agent.validate(gen, store,
                                        evidence="Model covers ~300 units / price points.")
    assert ok1 and not problems


# ---- greeting -----------------------------------------------------------

def test_wiki_summary_and_session_greeting(monkeypatch):
    monkeypatch.setattr(tg.compiler, "wiki_to_resume_yaml",
                        lambda: {"experience": [{"role": "Data Scientist"}],
                                 "skills": [{"category": "ML & GenAI"}]})
    s = tg.wiki_summary()
    assert "Data Scientist" in s and "ML & GenAI" in s

    monkeypatch.setattr(tg.llm, "chat", lambda *a, **k: "Welcome back! What role are you targeting?")
    g = tg.session_greeting({"provider": "anthropic", "model": "x"})
    assert "What role" in g


def test_session_greeting_guards_against_runaway_loops(monkeypatch):
    monkeypatch.setattr(tg.compiler, "wiki_to_resume_yaml",
                        lambda: {"experience": [], "skills": []})
    # a repetition blob (no sentence breaks) → degenerate → "" (static fallback)
    monkeypatch.setattr(tg.llm, "chat", lambda *a, **k: "share the job description " * 60)
    assert tg.session_greeting({"provider": "anthropic", "model": "x"}) == ""
    # good opening that then loops → keep the first 1-2 sentences, stay short
    monkeypatch.setattr(tg.llm, "chat",
                        lambda *a, **k: "Hi! What role are you targeting? " + "Please share it. " * 60)
    g = tg.session_greeting({"provider": "anthropic", "model": "x"})
    assert g.startswith("Hi! What role are you targeting?") and len(g) <= 320


# ---- end-to-end flows (llm stubbed) ------------------------------------

def test_refine_shows_sections_via_on_section_and_quit_aborts(monkeypatch):
    _patch_llm(monkeypatch, {
        tg.Assessment: tg.Assessment(role_title="T", requirements=[]),  # no gaps
        tg.Resume: tg.Resume(name="Ada", experience=[
            tg.Job(role="E", company="C", bullets=[tg.Bullet(text="did X")])])})
    monkeypatch.setattr(tg.compiler, "wiki_to_resume_yaml",
                        lambda: {"experience": [], "skills": []})
    monkeypatch.setattr(tg.store, "read_career_bundle", lambda: "wiki bundle")

    shown = []
    res = tg.run_think(
        {"provider": "anthropic", "model": "x"},
        opening="a sufficiently long target brief so understand skips its question",
        out_stem="t", ask_fn=lambda: "quit",       # quit at the section menu
        say_fn=lambda t: None, notify_fn=lambda t: None,
        render_fn=lambda d, s: ("d", "p"), allow_web=False,
        on_section=lambda title, body: shown.append((title, body)))

    assert res["aborted"] is True
    titles = [t for t, _ in shown]
    assert "strategy" in titles and "sections" in titles
    menu = [b for t, b in shown if t == "sections"][0]
    assert "E — C" in menu               # the role appears in the section menu


def test_gap_conversation_collects_provided_and_finalizes(monkeypatch, tmp_path):
    monkeypatch.setenv("RESUME_AGENT_HOME", str(tmp_path))
    _patch_llm(monkeypatch, {
        tg.Assessment: tg.Assessment(role_title="LLM Eng", requirements=[
            tg.Requirement(name="Kubernetes", importance="must", covered=False)]),
        tg.Resume: tg.Resume(name="Ada", experience=[tg.Job(role="E", company="C")])})
    monkeypatch.setattr(tg.compiler, "wiki_to_resume_yaml", lambda: {"experience": [], "skills": []})
    monkeypatch.setattr(tg.store, "read_career_bundle", lambda: "wiki bundle")

    answers = iter(["yes, ran prod k8s clusters at Acme", "done"])   # gap answer, then generate
    says = []
    res = tg.run_think(
        {"provider": "anthropic", "model": "x"},
        opening="a sufficiently long target brief so understand skips its question",
        out_stem="t", ask_fn=lambda: next(answers),
        say_fn=says.append, notify_fn=lambda t: None,
        render_fn=lambda d, s: ("d.docx", "p.pdf"), allow_web=False)

    assert res["aborted"] is False
    assert res["provided"] == ["Kubernetes: yes, ran prod k8s clusters at Acme"]
    assert any("Kubernetes" in s for s in says)              # the gap was surfaced
    assert any("look thin" in s for s in says)               # coverage reported


def test_is_clarifying():
    for q in ["what do you mean by data modelling?", "what is A/B testing",
              "can you explain", "idk", "not sure what that is", "huh?"]:
        assert tg.is_clarifying(q), q
    for a in ["yes I ran A/B tests at Acme", "no", "built data models for 3 years"]:
        assert not tg.is_clarifying(a), a


def test_convo_explains_a_term_then_accepts_the_answer(monkeypatch, tmp_path):
    """When the user asks what a requirement means, the agent explains it and
    re-asks the SAME short question instead of storing the question as an answer."""
    monkeypatch.setenv("RESUME_AGENT_HOME", str(tmp_path))
    _patch_llm(monkeypatch, {
        tg.Assessment: tg.Assessment(role_title="T", requirements=[
            tg.Requirement(name="data modeling", question="Have you designed data models?",
                           importance="must", covered=False)]),
        tg.Resume: tg.Resume(name="Ada", experience=[tg.Job(role="E", company="C")])},
        chat_text="Data modeling means structuring data into tables, e.g. a star schema.")
    monkeypatch.setattr(tg.compiler, "wiki_to_resume_yaml", lambda: {"experience": [], "skills": []})
    monkeypatch.setattr(tg.store, "read_career_bundle", lambda: "wiki")

    answers = iter(["what do you mean?", "yes, built star schemas at Acme", "done"])
    says = []
    res = tg.run_think({"provider": "anthropic", "model": "x"},
                       opening="a sufficiently long target brief so understand skips its question",
                       out_stem="t", ask_fn=lambda: next(answers),
                       say_fn=says.append, notify_fn=lambda t: None,
                       render_fn=lambda d, s: ("d", "p"), allow_web=False)
    assert res["aborted"] is False
    assert any("star schema" in s for s in says)                     # it explained the term
    assert sum("Have you designed data models?" in s for s in says) >= 2  # asked, then re-asked
    assert res["provided"] == ["data modeling: yes, built star schemas at Acme"]


def test_assess_reports_no_gaps_when_covered(monkeypatch):
    _patch_llm(monkeypatch, {
        tg.Assessment: tg.Assessment(role_title="T", requirements=[
            tg.Requirement(name="GenAI", importance="must", covered=True)]),
        tg.Resume: tg.Resume(name="Ada", experience=[tg.Job(role="E", company="C")])})
    monkeypatch.setattr(tg.compiler, "wiki_to_resume_yaml", lambda: {"experience": [], "skills": []})
    monkeypatch.setattr(tg.store, "read_career_bundle", lambda: "wiki")

    says = []
    tg.run_think({"provider": "anthropic", "model": "x"},
                 opening="a sufficiently long target brief so understand skips its question",
                 out_stem="t", ask_fn=lambda: "quit",
                 say_fn=says.append, notify_fn=lambda t: None,
                 render_fn=lambda d, s: ("d", "p"), allow_web=False)
    assert any("covers the key requirements" in s for s in says)


def test_quality_gaps_are_reported_and_asked(monkeypatch):
    _patch_llm(monkeypatch, {
        tg.Assessment: tg.Assessment(role_title="T",
            requirements=[tg.Requirement(name="GenAI", covered=True)],
            quality_gaps=["The DSPy insights work states 'measurable gains' but no metric"]),
        tg.Resume: tg.Resume(name="Ada", experience=[tg.Job(role="E", company="C")])})
    monkeypatch.setattr(tg.compiler, "wiki_to_resume_yaml", lambda: {"experience": [], "skills": []})
    monkeypatch.setattr(tg.store, "read_career_bundle", lambda: "wiki")

    answers = iter(["DSPy insights improved accuracy 12%", "done"])  # metrics answer, then generate
    says = []
    res = tg.run_think({"provider": "anthropic", "model": "x"},
                       opening="a sufficiently long target brief so understand skips its question",
                       out_stem="t", ask_fn=lambda: next(answers),
                       say_fn=says.append, notify_fn=lambda t: None,
                       render_fn=lambda d, s: ("d", "p"), allow_web=False)
    assert any("measurable gains" in s for s in says)            # quality gap surfaced
    assert any("real numbers" in s for s in says)                # metrics were requested
    assert any("12%" in p for p in res["provided"])              # user's metric captured


def test_refine_drop_preserves_other_roles_end_to_end(monkeypatch, tmp_path):
    """Dropping a bullet in one role used to delete whole other roles. Now an edit is
    section-scoped, so B and C survive."""
    monkeypatch.setenv("RESUME_AGENT_HOME", str(tmp_path))
    resume = tg.Resume(name="Ada", experience=[
        tg.Job(role="A", company="X", bullets=[tg.Bullet(text="a1"), tg.Bullet(text="a2"), tg.Bullet(text="a3")]),
        tg.Job(role="B", company="Y", bullets=[tg.Bullet(text="b1"), tg.Bullet(text="b2")]),
        tg.Job(role="C", company="Z", bullets=[tg.Bullet(text="c1")])])
    _patch_llm(monkeypatch, {
        tg.Assessment: tg.Assessment(role_title="T", requirements=[]),
        tg.Resume: resume})
    monkeypatch.setattr(tg.compiler, "wiki_to_resume_yaml", lambda: {"experience": [], "skills": []})
    monkeypatch.setattr(tg.store, "read_career_bundle", lambda: "wiki")

    # open role A (section 2), drop bullets 1 and 3, go back, then generate
    answers = iter(["2", "drop 1 3", "back", "done"])
    captured = {}
    res = tg.run_think(
        {"provider": "anthropic", "model": "x"},
        opening="a sufficiently long target brief so understand skips its question",
        out_stem="t", ask_fn=lambda: next(answers),
        say_fn=lambda t: None, notify_fn=lambda t: None,
        render_fn=lambda data, stem: (captured.update(data=data) or ("d", "p")),
        allow_web=False)

    assert res["aborted"] is False
    exp = captured["data"]["experience"]
    assert [b["text"] for b in exp[0]["bullets"]] == ["a2"]          # A: dropped 1 & 3
    assert [b["text"] for b in exp[1]["bullets"]] == ["b1", "b2"]    # B intact
    assert [b["text"] for b in exp[2]["bullets"]] == ["c1"]          # C intact (was lost before)


def test_resume_schema_round_trips():
    r = tg.Resume(name="Ada", experience=[
        tg.Job(role="Eng", company="Acme", bullets=[tg.Bullet(text="did X")])])
    assert r.model_dump()["experience"][0]["bullets"][0]["text"] == "did X"


def test_bullet_schema_has_no_tags():
    assert "tags" not in tg.Bullet.model_fields
    assert "tags" not in tg.SkillGroup.model_fields
