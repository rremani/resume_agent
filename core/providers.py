"""
Provider abstraction.

One interface, two backends (Anthropic native + OpenRouter). The agent core
talks only to `Provider`, never to a vendor SDK directly. This is the seam that
keeps the system model-agnostic and lets fast/think pick any model on either
backend — and later lets a bigger agent loop reuse the same interface.

Web search:
  - Anthropic backend uses the native server-side web_search tool.
  - OpenRouter backend uses :online model suffix / web plugin where supported.
Web search is only wired in when `allow_web=True` (Think mode passes this).
"""

from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class CompletionResult:
    text: str
    used_web: bool = False


class Provider:
    """Base interface. Subclasses implement `complete`."""
    def complete(self, system: str, user: str, *, model: str,
                 max_tokens: int = 4000, allow_web: bool = False) -> CompletionResult:
        raise NotImplementedError


class AnthropicProvider(Provider):
    def __init__(self, api_key: Optional[str] = None):
        from anthropic import Anthropic
        self._client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def complete(self, system, user, *, model, max_tokens=4000, allow_web=False):
        tools = []
        if allow_web:
            # Native server-side web search tool.
            tools = [{"type": "web_search_20250305", "name": "web_search"}]
        kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}])
        if tools:
            kwargs["tools"] = tools
        msg = self._client.messages.create(**kwargs)
        text = "".join(getattr(b, "text", "") for b in msg.content
                       if getattr(b, "type", "") == "text")
        used_web = any(getattr(b, "type", "") in
                       ("server_tool_use", "web_search_tool_result")
                       for b in msg.content)
        return CompletionResult(text=text.strip(), used_web=used_web)


class OpenRouterProvider(Provider):
    """OpenRouter via its OpenAI-compatible endpoint."""
    BASE = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: Optional[str] = None):
        from openai import OpenAI
        self._client = OpenAI(
            base_url=self.BASE,
            api_key=api_key or os.environ.get("OPENROUTER_API_KEY"),
        )

    def complete(self, system, user, *, model, max_tokens=4000, allow_web=False):
        # OpenRouter enables web search by appending ':online' to the model slug
        # (or via the web plugin). We use the suffix form for simplicity.
        used_web = False
        effective_model = model
        if allow_web and not model.endswith(":online"):
            effective_model = model + ":online"
            used_web = True
        resp = self._client.chat.completions.create(
            model=effective_model,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return CompletionResult(text=resp.choices[0].message.content.strip(),
                                used_web=used_web)


def make_provider(name: str, api_key: Optional[str] = None) -> Provider:
    name = (name or "").lower()
    if name == "anthropic":
        return AnthropicProvider(api_key)
    if name == "openrouter":
        return OpenRouterProvider(api_key)
    raise ValueError(f"Unknown provider: {name!r} (use 'anthropic' or 'openrouter')")
