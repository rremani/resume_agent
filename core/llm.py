"""
The single model layer — one interface over every provider, via LiteLLM.

Both paths use this: `chat()` for plain-text completions (fast mode + compile)
and `structured()` for validated Pydantic output (think mode). LiteLLM routes by
model string and reads API keys from the environment, so supporting a new
provider is a config change, not code — put a full LiteLLM model string in
`config.model` (e.g. "gemini/gemini-2.5-pro", "groq/llama-3.1-70b",
"ollama/llama3") and set the matching *_API_KEY.
"""
from __future__ import annotations

import json
import re

# LiteLLM is heavy to import (~seconds), so it is loaded LAZILY on the first
# model call — commands that make no LLM call (onboard, status, --help) stay fast.
_litellm = None


def _lm():
    global _litellm
    if _litellm is None:
        import litellm
        # Quiet, resilient defaults: no telemetry; drop params a given model
        # doesn't support (e.g. max_tokens on some endpoints) instead of erroring.
        litellm.telemetry = False
        litellm.drop_params = True
        litellm.suppress_debug_info = True
        _litellm = litellm
    return _litellm

# LiteLLM provider prefixes we recognise in a bare model string.
_PREFIXES = {
    "openrouter", "anthropic", "openai", "azure", "gemini", "vertex_ai", "groq",
    "ollama", "bedrock", "cohere", "mistral", "together_ai", "fireworks_ai",
    "deepseek", "xai", "perplexity", "replicate", "huggingface",
}


def to_model(provider: str, model: str) -> str:
    """Map config (provider, model) → a LiteLLM model string. If `model` already
    carries a known provider prefix, use it verbatim (this is how any LiteLLM
    provider can be selected). Otherwise prefix by the configured provider."""
    provider = (provider or "").lower()
    head = model.split("/", 1)[0] if "/" in model else ""
    if head in _PREFIXES:
        return model                       # already a full LiteLLM string
    if provider in _PREFIXES:
        return f"{provider}/{model}"       # prefix by the configured provider
    return model   # pass through — LiteLLM infers from the string / env


def _messages(system: str, user: str) -> list:
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def _opts(model: str) -> dict:
    """Turn OFF reasoning/thinking so reasoning models (GLM, deepseek-r1, …) answer
    DIRECTLY — faster, cheaper, and their `content` isn't left empty while they
    think. OpenRouter exposes this via the `reasoning` control; other providers
    aren't reasoning-by-default, so nothing is added."""
    if model.startswith("openrouter/"):
        return {"extra_body": {"reasoning": {"enabled": False}}}
    return {}


def chat(system: str, user: str, *, model: str, max_tokens: int = 4096) -> str:
    """A plain-text completion. `model` is a LiteLLM model string."""
    resp = _lm().completion(model=model, messages=_messages(system, user),
                            max_tokens=max_tokens, **_opts(model))
    return (resp.choices[0].message.content or "").strip()


def _extract_json(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):                     # strip ``` / ```json fences
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    return text


def structured(system: str, user: str, schema, *, model: str, max_tokens: int = 8192):
    """A completion validated against a Pydantic `schema` (returns an instance).

    Uses LiteLLM's response_format so capable providers enforce the JSON schema;
    a fenced/whitespace-wrapped reply is tolerated before validation."""
    resp = _lm().completion(model=model, messages=_messages(system, user),
                            max_tokens=max_tokens, response_format=schema, **_opts(model))
    content = _extract_json(resp.choices[0].message.content)
    return schema.model_validate(json.loads(content))
