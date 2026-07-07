"""
Web-search tool abstraction — an explicit, inspectable alternative to the
provider's opaque built-in web search.

Same shape as core/providers.py: ONE interface (`SearchTool`), swappable
backends (Tavily / Exa / Brave) chosen via config.yaml, key read from a
git-ignored .env. The `think`-mode agent uses this to research a target
company / pull a JD / verify a fact before tailoring; `fast` mode never
touches it (gating lives in core/agent.gather_research).

No third-party HTTP dependency: requests go through stdlib urllib so the tool
stays transparent and bundles cleanly into the frozen binary. Each backend is
~10 lines because the interface does the rest — that is the swappability the
design calls for.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional


class SearchError(RuntimeError):
    """Any failure building or running a search (bad provider, missing key, HTTP)."""


@dataclass
class SearchResult:
    title: str
    url: str
    content: str
    score: Optional[float] = None


# ---- stdlib HTTP helpers (monkeypatchable in tests) --------------------

def _post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    return _send(req)


def _get_json(url: str, params: dict, headers: dict | None = None) -> dict:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}", headers=headers or {}, method="GET")
    return _send(req)


def _send(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # network, HTTP error, bad JSON — all become SearchError
        raise SearchError(f"search request failed: {e}") from e


# ---- interface ----------------------------------------------------------

class SearchTool:
    """Base interface. Subclasses set ENV_VAR and implement `search`."""
    ENV_VAR: str = ""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or (os.environ.get(self.ENV_VAR) if self.ENV_VAR else None)

    def _require_key(self):
        if not self.api_key:
            raise SearchError(
                f"no API key for {type(self).__name__} "
                f"(set {self.ENV_VAR} in your .env)")

    def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        raise NotImplementedError


class TavilyProvider(SearchTool):
    ENV_VAR = "TAVILY_API_KEY"
    ENDPOINT = "https://api.tavily.com/search"

    def search(self, query, *, max_results=5):
        self._require_key()
        data = _post_json(self.ENDPOINT, {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        })
        return [SearchResult(title=r.get("title", ""), url=r.get("url", ""),
                             content=r.get("content", ""), score=r.get("score"))
                for r in data.get("results", [])]


class ExaProvider(SearchTool):
    ENV_VAR = "EXA_API_KEY"
    ENDPOINT = "https://api.exa.ai/search"

    def search(self, query, *, max_results=5):
        self._require_key()
        data = _post_json(self.ENDPOINT, {
            "query": query,
            "numResults": max_results,
            "contents": {"text": True},
        }, headers={"x-api-key": self.api_key})
        return [SearchResult(title=r.get("title", ""), url=r.get("url", ""),
                             content=r.get("text", "") or r.get("snippet", ""),
                             score=r.get("score"))
                for r in data.get("results", [])]


class BraveProvider(SearchTool):
    ENV_VAR = "BRAVE_API_KEY"
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def search(self, query, *, max_results=5):
        self._require_key()
        data = _get_json(self.ENDPOINT, {"q": query, "count": max_results},
                         headers={"X-Subscription-Token": self.api_key,
                                  "Accept": "application/json"})
        results = (data.get("web", {}) or {}).get("results", [])
        return [SearchResult(title=r.get("title", ""), url=r.get("url", ""),
                             content=r.get("description", ""))
                for r in results]


# ---- registry / factory -------------------------------------------------

SEARCH_PROVIDERS = {
    "tavily": TavilyProvider,
    "exa": ExaProvider,
    "brave": BraveProvider,
}

# Convenience: {provider_name: required_env_var} for onboarding.
SEARCH_ENV_VAR = {name: cls.ENV_VAR for name, cls in SEARCH_PROVIDERS.items()}


def make_search_tool(name: str, api_key: Optional[str] = None) -> SearchTool:
    cls = SEARCH_PROVIDERS.get((name or "").lower())
    if cls is None:
        raise SearchError(
            f"Unknown search provider {name!r} "
            f"(choose one of: {', '.join(SEARCH_PROVIDERS)})")
    return cls(api_key)


def from_config(cfg: dict | None, api_key: Optional[str] = None) -> Optional[SearchTool]:
    """Build the configured search tool, or None if search is off / unkeyed.

    Reads cfg['search']['provider']. Returns None (rather than raising) when no
    provider is configured or its key is absent — search is an optional add-on,
    so a missing key degrades gracefully to 'no explicit search'."""
    sc = (cfg or {}).get("search") or {}
    name = (sc.get("provider") or "").lower()
    if not name or name == "none":
        return None
    try:
        tool = make_search_tool(name, api_key)
    except SearchError:
        return None
    if not tool.api_key:
        return None
    return tool


def format_results(results: list[SearchResult], *, limit: int | None = None) -> str:
    """Render results as a compact, inspectable context block for the prompt."""
    items = results[:limit] if limit else results
    lines = []
    for i, r in enumerate(items, 1):
        snippet = " ".join((r.content or "").split())
        if len(snippet) > 500:
            snippet = snippet[:500] + "…"
        lines.append(f"[{i}] {r.title} — {r.url}\n{snippet}")
    return "\n\n".join(lines)
