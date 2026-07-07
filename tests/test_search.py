"""Tests for the pluggable web-search tool (core/search.py) and its gating.

The tool mirrors the Provider pattern: one interface, swappable backends
selected via config, key from env. It must be usable in `think` mode and
NEVER produce a web call in `fast` mode.
"""
import os
import sys

import pytest

CORE_PARENT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, CORE_PARENT)
from core import search, agent  # noqa: E402


# ---- factory / swappability --------------------------------------------

def test_factory_builds_each_known_provider():
    for name, cls in (("tavily", search.TavilyProvider),
                      ("exa", search.ExaProvider),
                      ("brave", search.BraveProvider)):
        tool = search.make_search_tool(name, api_key="k")
        assert isinstance(tool, cls)


def test_factory_is_case_insensitive():
    assert isinstance(search.make_search_tool("Tavily", api_key="k"),
                      search.TavilyProvider)


def test_unknown_provider_raises():
    with pytest.raises(search.SearchError):
        search.make_search_tool("googol", api_key="k")


def test_missing_key_raises_on_search(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    tool = search.make_search_tool("tavily")  # no key anywhere
    with pytest.raises(search.SearchError):
        tool.search("anything")


# ---- per-provider request/response mapping (HTTP mocked) ----------------

def test_tavily_maps_request_and_response(monkeypatch):
    captured = {}

    def fake_post(url, payload, headers=None):
        captured["url"] = url
        captured["payload"] = payload
        return {"results": [
            {"title": "Acme", "url": "https://acme.com",
             "content": "Acme builds rockets.", "score": 0.9},
        ]}

    monkeypatch.setattr(search, "_post_json", fake_post)
    tool = search.TavilyProvider(api_key="secret")
    results = tool.search("acme company", max_results=3)

    assert "tavily.com" in captured["url"]
    assert captured["payload"]["query"] == "acme company"
    assert captured["payload"]["max_results"] == 3
    assert captured["payload"]["api_key"] == "secret"
    assert len(results) == 1
    r = results[0]
    assert (r.title, r.url) == ("Acme", "https://acme.com")
    assert "rockets" in r.content


def test_brave_maps_request_and_response(monkeypatch):
    captured = {}

    def fake_get(url, params, headers=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return {"web": {"results": [
            {"title": "Beta", "url": "https://beta.com",
             "description": "Beta does AI."},
        ]}}

    monkeypatch.setattr(search, "_get_json", fake_get)
    tool = search.BraveProvider(api_key="brave-key")
    results = tool.search("beta", max_results=2)

    assert "brave.com" in captured["url"]
    assert captured["params"]["q"] == "beta"
    assert captured["params"]["count"] == 2
    assert captured["headers"]["X-Subscription-Token"] == "brave-key"
    assert results[0].title == "Beta"
    assert "AI" in results[0].content


def test_exa_maps_request_and_response(monkeypatch):
    captured = {}

    def fake_post(url, payload, headers=None):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return {"results": [
            {"title": "Gamma", "url": "https://gamma.com", "text": "Gamma text."},
        ]}

    monkeypatch.setattr(search, "_post_json", fake_post)
    tool = search.ExaProvider(api_key="exa-key")
    results = tool.search("gamma", max_results=5)

    assert "exa.ai" in captured["url"]
    assert captured["payload"]["query"] == "gamma"
    assert captured["headers"]["x-api-key"] == "exa-key"
    assert results[0].title == "Gamma"
    assert "text" in results[0].content


# ---- config-driven selection -------------------------------------------

def test_from_config_none_when_unset(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    assert search.from_config({}) is None
    assert search.from_config({"search": {"provider": "none"}}) is None


def test_from_config_builds_selected_provider(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    tool = search.from_config({"search": {"provider": "tavily"}})
    assert isinstance(tool, search.TavilyProvider)


def test_from_config_none_when_key_missing(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    # provider configured but no key → degrade gracefully to None, not crash
    assert search.from_config({"search": {"provider": "exa"}}) is None


# ---- the hard gate: NO web in fast mode --------------------------------

class SpyTool(search.SearchTool):
    def __init__(self):
        self.calls = 0

    def search(self, query, *, max_results=5):
        self.calls += 1
        return [search.SearchResult(title="X", url="http://x", content="ctx snippet")]


def test_gather_research_silent_and_no_call_when_web_disabled():
    spy = SpyTool()
    out = agent.gather_research(spy, "research acme", allow_web=False)
    assert out == ""
    assert spy.calls == 0, "fast mode must never hit the search tool"


def test_gather_research_runs_when_web_enabled():
    spy = SpyTool()
    out = agent.gather_research(spy, "research acme", allow_web=True)
    assert spy.calls == 1
    assert "ctx snippet" in out


def test_gather_research_no_tool_is_noop():
    assert agent.gather_research(None, "research acme", allow_web=True) == ""


# ---- integration: run_once gating through the real pipeline -------------

from core.providers import CompletionResult  # noqa: E402


class StubProvider:
    """Returns a fixed resume YAML; records the prompt and web flag."""
    def __init__(self):
        self.last_user = None

    def complete(self, system, user, *, model, max_tokens=4000, allow_web=False):
        self.last_user = user
        return CompletionResult(
            text="name: Ada Lovelace\ntitle: Engineer\n"
                 "contact:\n  email: a@b.com\nsummary: hi\n"
                 "skills: []\nexperience: []\neducation: []\n",
            used_web=allow_web)


def _seed_minimal_wiki():
    from core import store
    store.write_career_file("profile.md",
                            {"name": "Ada Lovelace", "title": "Engineer",
                             "contact": {"email": "a@b.com"}, "education": []},
                            "A summary.")
    store.write_career_file("skills.md", {"skills": []}, "skills")
    store.write_career_file("roles/acme.md",
                            {"role": "Engineer", "company": "Acme",
                             "start": "01/2020", "end": "Present",
                             "location": "London", "bullets": []}, "")


def test_run_once_fast_makes_no_search_call(monkeypatch, tmp_path):
    monkeypatch.setenv("RESUME_AGENT_HOME", str(tmp_path))
    _seed_minimal_wiki()
    spy = SpyTool()
    res = agent.run_once(StubProvider(), model="m", brief="research Acme Corp",
                         allow_web=False, out_stem="t", do_render=False,
                         search_tool=spy)
    assert spy.calls == 0
    assert res["used_search"] is False


def test_run_once_think_injects_research(monkeypatch, tmp_path):
    monkeypatch.setenv("RESUME_AGENT_HOME", str(tmp_path))
    _seed_minimal_wiki()
    spy = SpyTool()
    prov = StubProvider()
    res = agent.run_once(prov, model="m", brief="research Acme Corp",
                         allow_web=True, out_stem="t", do_render=False,
                         search_tool=spy)
    assert spy.calls == 1
    assert res["used_search"] is True
    assert "RESEARCH CONTEXT" in prov.last_user
    assert "ctx snippet" in prov.last_user
