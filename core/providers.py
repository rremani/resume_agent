"""
Provider abstraction — a thin shim over the unified LiteLLM layer (core/llm.py).

Kept so the fast/compile callers (`agent`, `compiler`, `ingest`) don't change:
they still call `make_provider(cfg["provider"]).complete(...)`. Routing to any
provider now happens in core/llm.py.

Note: provider built-in web search was removed — research runs through the
explicit search tool (core/search.py), so `used_web` is always False here.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from . import llm


@dataclass
class CompletionResult:
    text: str
    used_web: bool = False


class Provider:
    """A named backend; `complete` routes through LiteLLM."""
    def __init__(self, name: str):
        self.name = name

    def complete(self, system: str, user: str, *, model: str,
                 max_tokens: int = 4000, allow_web: bool = False) -> CompletionResult:
        # allow_web (provider built-in search) is intentionally ignored; the
        # explicit search tool handles research.
        text = llm.chat(system, user, model=llm.to_model(self.name, model),
                        max_tokens=max_tokens)
        return CompletionResult(text=text, used_web=False)


def make_provider(name: str, api_key: Optional[str] = None) -> Provider:
    # Keys are read from the environment by LiteLLM; api_key is accepted for
    # backward compatibility but unused. Any name is allowed — put a full
    # LiteLLM model string in config.model to reach other providers.
    return Provider((name or "").lower())
