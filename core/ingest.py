"""
Ingestion flows.

bootstrap_from_text(resume_text):
    First-run. Take the text of an existing resume (extracted from PDF), store it
    as the first immutable raw entry, then compile the initial career wiki.

add_interactive(...):
    A short chat. The user describes something new (a consulting project, a cert,
    a skill). The agent asks free-form follow-up questions until it has enough,
    then writes ONE new immutable raw entry and recompiles the wiki.

The agent picks its own questions (per the chosen design), guided only by a goal:
gather the facts a resume bullet needs (what, for whom, scale/impact, tech, when).
"""

from __future__ import annotations
from . import store, compiler
from .providers import Provider, CompletionResult


def bootstrap_from_text(provider: Provider, *, model: str, resume_text: str):
    if not store.raw_is_empty():
        raise RuntimeError("raw/ already has sources — bootstrap is for first run only.")
    store.add_raw("resume", "original-resume", resume_text)
    return compiler.compile_wiki(provider, model=model)


# ---- conversational add -------------------------------------------------

ADD_SYSTEM = """You are helping a candidate add a NEW item to their career history \
(a project, certificate, skill, or role). Your job is to gather enough concrete \
detail to later write strong resume bullets, then stop.

A good resume bullet needs: WHAT was done, FOR WHOM / in what context, the SCALE or \
quantified IMPACT, the TECH/tools used, and WHEN. Ask about whatever is still \
missing. Ask ONE focused question at a time. Keep it brief and natural.

When you have enough (typically 2-5 exchanges), respond with EXACTLY:
READY
<a clean, factual markdown summary of the new item, including every concrete detail \
the user gave — especially numbers/metrics/dates/tech. Do not invent anything.>

Until then, respond with only your next question."""


def add_interactive(provider: Provider, *, model: str, opening: str, ask_fn, say_fn,
                    allow_web: bool = False):
    """Run the add chat.
    ask_fn() -> user's next line (str). say_fn(text) -> show agent text.
    Returns the path of the new raw entry, or None if aborted.
    """
    history = [f"USER: {opening}"]
    while True:
        convo = "\n".join(history)
        res = provider.complete(
            ADD_SYSTEM,
            convo + "\n\nYour next message (a question, or 'READY' + summary):",
            model=model, max_tokens=1200, allow_web=allow_web,
        )
        reply = res.text.strip()

        if reply.upper().startswith("READY"):
            summary = reply.split("\n", 1)[1].strip() if "\n" in reply else ""
            # derive a title from the opening line
            title = opening.strip().split("\n")[0][:60] or "new-item"
            kind = _guess_kind(opening + " " + summary)
            path = store.add_raw(kind, title, summary, extra={"source": "add-chat"})
            return path

        say_fn(reply)
        user = ask_fn()
        if user.strip().lower() in ("quit", "exit", "cancel"):
            return None
        history.append(f"ASSISTANT: {reply}")
        history.append(f"USER: {user}")


def _guess_kind(text: str) -> str:
    t = text.lower()
    if "certificat" in t or "certified" in t or "credential" in t:
        return "cert"
    if "skill" in t and "project" not in t:
        return "skill"
    if "role" in t or "joined" in t or "position" in t:
        return "role"
    return "project"
