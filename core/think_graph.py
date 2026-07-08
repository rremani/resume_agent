"""
Think mode — a LangGraph "blueprint, then fill" editor workflow.

    START → understand → blueprint → fill → improve → (done?) ─yes→ finalize → END
                                              ▲           │
                                              └─── edit ──┘   (feedback → one change)

Tailors the candidate's EXISTING verified experience to a target. It never
invents, never solicits new work, and never touches the knowledge base —
capturing new experience is a separate flow (`resume add`).

- understand: pin the target (ONE simple question if the brief is thin); load the
  FULL vetted wiki as evidence and a LIGHT inventory (titles/categories/projects).
- blueprint:  design the SHAPE of a strong resume for this target from the
  inventory — ordered themes, summary angle, lead skills. Strategy, not copy.
- fill:       select the candidate's REAL bullets/projects into that blueprint.
  A theme with no supporting evidence is dropped, never fabricated. (Small wiki →
  fed whole → no retrieval.)
- improve:    show a concise preview (prefixed with the blueprint) and ask ONE
  simple question. The user drives changes and says when done.
- edit:       apply ONE change to the current draft (surgical — no re-draft).
- finalize:   ground-check and render. No knowledge-base writes.
"""
from __future__ import annotations

import os
import glob
import yaml
from typing import Optional
from typing_extensions import TypedDict
from pydantic import BaseModel, Field

from langgraph.graph import StateGraph, START, END
from langchain_core.messages import SystemMessage, HumanMessage

from . import store, compiler, paths, agent as core_agent, search as search_mod

MAX_ROUNDS = 8  # safety cap on the improve loop


# ---- resume schema (structured output) ---------------------------------

# No `tags` field: tags are never used when tailoring (render's tag filter runs
# with wanted=None, so everything passes) — emitting a tags[] array per bullet/
# skill just inflated the structured output toward the token cap. Leaner schema =
# smaller, cheaper, less-likely-to-truncate output.
class Bullet(BaseModel):
    text: str


class SkillGroup(BaseModel):
    category: str
    items: list[str] = Field(default_factory=list)


class Job(BaseModel):
    role: str
    company: str
    start: str = ""
    end: str = ""
    location: str = ""
    context: Optional[str] = None
    bullets: list[Bullet] = Field(default_factory=list)


class Education(BaseModel):
    degree: str
    institution: str
    start: str = ""
    end: str = ""
    location: str = ""


class Contact(BaseModel):
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    medium: str = ""


class Resume(BaseModel):
    """The tailored resume. Every fact must match the candidate's store exactly."""
    name: str
    title: str = ""
    contact: Contact = Field(default_factory=Contact)
    summary: str = ""
    skills: list[SkillGroup] = Field(default_factory=list)
    experience: list[Job] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)


# ---- blueprint schema (the planned shape, before evidence is filled in) ----

class Theme(BaseModel):
    name: str            # e.g. "Agentic AI & LLM orchestration"
    why: str = ""        # one line: why it matters for this target
    priority: int = 1    # 1 = lead with this


class Blueprint(BaseModel):
    """The intended shape of the resume for a target — strategy, not copy."""
    title: str = ""                              # target-facing title
    summary_angle: str = ""                      # the narrative the summary takes
    themes: list[Theme] = Field(default_factory=list)   # ordered; what to foreground
    lead_skills: list[str] = Field(default_factory=list)  # skill clusters to surface first


BLUEPRINT_SYSTEM = """You are a resume STRATEGIST. Given a target role and a LIGHT \
INVENTORY of the candidate's real experience (role titles, skill categories, and \
project names — no details), design the SHAPE of a strong resume for THIS target.

- Choose the themes a strong candidate for this role foregrounds — but ONLY themes \
the candidate can actually support per the inventory. Never propose a theme with no \
inventory backing.
- Order themes by priority (1 = lead with this).
- Give the summary an angle aimed squarely at the target.
- Pick which skill categories to surface first.

You are planning strategy, not writing copy — do not write bullets or invent \
metrics. Output the structured blueprint."""


FILL_SYSTEM = """You are an expert resume EDITOR filling a planned blueprint with \
the candidate's REAL evidence — an editor, never an author.

- You are given a BLUEPRINT (the intended shape: title, summary angle, ordered \
themes, lead skills) and the candidate's FULL VERIFIED EXPERIENCE.
- For each theme, SELECT and reword the candidate's real bullets/projects that \
evidence it. Order roles and bullets to foreground the high-priority themes, \
strongest bullet first within each role.
- Be concise — a recruiter skims, so keep the resume tight and skimmable (aim for \
1-2 pages). Give the roles most relevant to THIS target more depth, and trim \
older or less-relevant roles to only their strongest points. You decide how many \
bullets each role warrants by its relevance — prefer fewer sharp bullets over \
exhaustive lists.
- Keep the COMPLETE role history — tailor by emphasis, not deletion.
- A theme with NO supporting evidence is DROPPED, never fabricated. Do not stretch \
unrelated work to fill a theme.
- Facts are LOCKED: every metric/number, company, title, date, institution, and \
named technology must come from the verified experience and match it exactly \
(including punctuation in titles).
- The summary follows the blueprint's angle but claims ONLY what the candidate has \
actually done. Never echo the target's requirements as qualifications; no \
aspirational claims; NO hedging tails ("analogous to …", "directly applicable to …").

Output the structured resume."""


DRAFT_SYSTEM = """You are an expert resume EDITOR. You are given a candidate's \
VERIFIED experience, a target role, and the CURRENT DRAFT, and you apply ONE \
requested change — an editor, never an author.

- Work ONLY from the verified experience provided. Do not invent, assume, or ask \
for anything not there. If the requested change isn't supported by the verified \
experience, do NOT add it.
- Apply ONLY the requested change; keep everything else in the current draft \
exactly as-is (same roles, bullets, order, numbers).
- Facts are LOCKED: every metric/number, company, title, date, institution, and \
named technology must come from the verified experience and match it exactly \
(including punctuation in titles).
- No invented skills/domains, no echoing the target's requirements, no aspirational \
claims, no hedging tails.

Output the structured resume."""


# ---- model builder ------------------------------------------------------

# A full-resume structured-output JSON (all roles, bullets, tags) can exceed
# 4k tokens; too low a cap truncates the JSON mid-object and the structured-output
# parser then raises LengthFinishReasonError. 8192 gives comfortable headroom.
MAX_OUTPUT_TOKENS = 8192


def _build_model(cfg: dict, model_id: str):
    provider = (cfg.get("provider") or "anthropic").lower()
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model_id, max_tokens=MAX_OUTPUT_TOKENS)
    if provider == "openrouter":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model_id, max_tokens=MAX_OUTPUT_TOKENS,
                          base_url="https://openrouter.ai/api/v1",
                          api_key=os.environ.get("OPENROUTER_API_KEY"))
    raise ValueError(f"Unknown provider {provider!r}")


# ---- inventory (deterministic, no LLM) ---------------------------------

def build_inventory() -> str:
    """A light index of the wiki — role titles, skill categories, project names.
    No bullets, no LLM. Feeds the blueprint (strategy) step so it plans a shape
    the candidate can actually support."""
    resume = compiler.wiki_to_resume_yaml()
    lines = ["ROLES:"]
    for j in resume.get("experience", []):
        lines.append(f"  - {j.get('role','')} — {j.get('company','')} "
                     f"({j.get('start','')}–{j.get('end','')})")
    cats = [g.get("category", "") for g in resume.get("skills", [])]
    lines.append("SKILL CATEGORIES: " + ", ".join(c for c in cats if c))
    projs = []
    proj_dir = os.path.join(paths.career_dir(), "projects")
    for p in sorted(glob.glob(os.path.join(proj_dir, "*.md"))):
        name = None
        for line in open(p).read().splitlines():
            if line.strip().startswith("name:"):
                name = line.split("name:", 1)[1].strip()
                break
        projs.append(name or os.path.splitext(os.path.basename(p))[0])
    if projs:
        lines.append("PROJECTS: " + ", ".join(projs))
    return "\n".join(lines)


# ---- LLM steps (module-level, testable with a stub model) --------------

def blueprint_resume(model, target: str, inventory: str) -> Blueprint:
    """Design the resume's shape for the target from the light inventory."""
    user = [f"=== TARGET ===\n{target}",
            "=== CANDIDATE INVENTORY (light index — titles / categories / "
            f"projects only) ===\n{inventory}",
            "Design the resume blueprint now."]
    structured = model.with_structured_output(Blueprint)
    return structured.invoke([SystemMessage(content=BLUEPRINT_SYSTEM),
                              HumanMessage(content="\n\n".join(user))])


def fill_resume(model, blueprint: dict, evidence: str) -> Resume:
    """Fill the blueprint with the candidate's real, verified evidence."""
    user = ["=== BLUEPRINT (the intended shape) ===\n"
            + yaml.safe_dump(blueprint, sort_keys=False, allow_unicode=True),
            "=== CANDIDATE'S VERIFIED EXPERIENCE (the ONLY source of facts) ===\n"
            + evidence,
            "Fill the blueprint with the candidate's real evidence now."]
    structured = model.with_structured_output(Resume)
    return structured.invoke([SystemMessage(content=FILL_SYSTEM),
                              HumanMessage(content="\n\n".join(user))])


def draft_resume(model, target: str, evidence: str, feedback: str = "",
                 current: Optional[dict] = None) -> Resume:
    """Apply ONE change to the current draft, editing only verified material."""
    user = [f"=== TARGET ===\n{target}",
            f"=== CANDIDATE'S VERIFIED EXPERIENCE (the ONLY source of facts) ===\n{evidence}"]
    if current is not None and feedback and feedback not in ("done", ""):
        user.append("=== CURRENT DRAFT (edit THIS; keep everything not mentioned) ===\n"
                    + yaml.safe_dump(current, sort_keys=False, allow_unicode=True)[:6000])
        user.append(f"=== APPLY ONLY THIS CHANGE ===\n{feedback}")
    user.append("Produce the tailored resume now.")
    structured = model.with_structured_output(Resume)
    return structured.invoke([SystemMessage(content=DRAFT_SYSTEM),
                              HumanMessage(content="\n\n".join(user))])


def preview(draft: dict, blueprint: Optional[dict] = None) -> str:
    lines = []
    if blueprint:
        themes = " · ".join(t.get("name", "") for t in blueprint.get("themes", [])[:4])
        head = "Tailoring toward: " + (blueprint.get("title", "") or "your target")
        if themes:
            head += f" — leading with {themes}"
        lines += [head, ""]
    lines += [f"DRAFT — {draft.get('name', '')}", "",
              "Summary: " + (draft.get("summary", "") or "")[:280], "", "Experience:"]
    for j in draft.get("experience", []):
        n = len(j.get("bullets", []))
        lines.append(f"  • {j.get('role','')} — {j.get('company','')} "
                     f"({j.get('start','')}–{j.get('end','')}) [{n} bullets]")
    lines.append("Skills: " + ", ".join(g.get("category", "")
                                        for g in draft.get("skills", [])))
    return "\n".join(lines)


# ---- graph state + routers ---------------------------------------------

class State(TypedDict, total=False):
    target: str
    inventory: str
    evidence: str
    blueprint: dict
    draft: dict
    feedback: str
    rounds: int
    aborted: bool
    out_stem: str
    ok: bool
    problems: list
    yaml: str
    docx: str
    pdf: str


def _route_understand(state: State):
    return END if state.get("aborted") else "blueprint"


def _route_improve(state: State):
    fb = state.get("feedback", "")
    if fb == "__abort__":
        return END
    if fb == "done" or state.get("rounds", 0) >= MAX_ROUNDS:
        return "finalize"
    return "edit"


# ---- orchestrator -------------------------------------------------------

def run_think(cfg: dict, *, opening: str, out_stem: str, ask_fn, say_fn,
              notify_fn, render_fn, allow_web: bool = True) -> dict:
    """Run the blueprint→fill workflow → grounded resume → render."""
    # Build the model lazily: the first question (understand) needs no LLM, so
    # deferring construction (and the heavy langchain_openai/anthropic import it
    # triggers) until the blueprint step lets the first prompt appear ~1s sooner.
    _cache = {}

    def model():
        if "m" not in _cache:
            _cache["m"] = _build_model(cfg, cfg["modes"]["think"]["model"])
        return _cache["m"]

    def understand(state: State) -> dict:
        target = (state.get("target") or "").strip()
        if len(target) < 40:
            say_fn("What role or company are you targeting?")
            ans = (ask_fn() or "").strip()
            if ans.lower() in ("quit", "exit", "cancel"):
                return {"aborted": True}
            target = (target + "\n" + ans).strip()
        # A short target (not a pasted JD) benefits from a single web lookup.
        if allow_web and len(target) < 120:
            tool = search_mod.from_config(cfg)
            if tool is not None:
                notify_fn("researching the target…")
                try:
                    web = search_mod.format_results(tool.search(target, max_results=3))
                except search_mod.SearchError:
                    web = ""
                if web:
                    target += ("\n\n[TARGET RESEARCH — framing/keywords only, "
                               "NOT resume facts]\n" + web)
        notify_fn("loading your verified experience…")
        return {"target": target, "evidence": store.read_career_bundle(),
                "inventory": build_inventory(), "rounds": 0, "aborted": False}

    def blueprint(state: State) -> dict:
        notify_fn("planning the resume for this target…")
        bp = blueprint_resume(model(), state["target"], state["inventory"])
        return {"blueprint": bp.model_dump()}

    def fill(state: State) -> dict:
        notify_fn("selecting your best-matching experience…")
        resume = fill_resume(model(), state["blueprint"], state["evidence"])
        return {"draft": resume.model_dump()}

    def improve(state: State) -> dict:
        say_fn(preview(state["draft"], state.get("blueprint")))
        say_fn("Anything to change? (reorder, reword, emphasise) — "
               "or 'done' to generate, 'quit' to abort.")
        fb = (ask_fn() or "").strip()
        low = fb.lower()
        rounds = state.get("rounds", 0) + 1
        if low in ("quit", "exit", "cancel"):
            return {"feedback": "__abort__", "rounds": rounds}
        if low in ("done", "go", "ok", "generate", ""):
            return {"feedback": "done", "rounds": rounds}
        return {"feedback": fb, "rounds": rounds}

    def edit(state: State) -> dict:
        notify_fn("applying your change…")
        resume = draft_resume(model(), state["target"], state["evidence"],
                              feedback=state.get("feedback", ""),
                              current=state.get("draft"))
        return {"draft": resume.model_dump()}

    def finalize(state: State) -> dict:
        from skill.render import _sanitize
        generated = _sanitize(state["draft"])   # strip the em-dash tell before persisting
        store_resume = compiler.wiki_to_resume_yaml()
        # Validate numbers against the SAME bundle the agent drafted from (incl.
        # project pages) — not the thin resume YAML, which drops project metrics.
        ok, problems = core_agent.validate(generated, store_resume,
                                           evidence=state.get("evidence", ""))
        out_dir = paths.output_dir()
        os.makedirs(out_dir, exist_ok=True)
        yaml_path = os.path.join(out_dir, f"{state['out_stem']}.yaml")
        with open(yaml_path, "w") as f:
            yaml.safe_dump(generated, f, sort_keys=False, allow_unicode=True, width=100)
        docx, pdf = render_fn(generated, state["out_stem"])
        return {"ok": ok, "problems": problems, "yaml": yaml_path,
                "docx": docx, "pdf": pdf}

    builder = StateGraph(State)
    builder.add_node("understand", understand)
    builder.add_node("blueprint", blueprint)
    builder.add_node("fill", fill)
    builder.add_node("improve", improve)
    builder.add_node("edit", edit)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "understand")
    builder.add_conditional_edges("understand", _route_understand,
                                  {"blueprint": "blueprint", END: END})
    builder.add_edge("blueprint", "fill")
    builder.add_edge("fill", "improve")
    builder.add_conditional_edges("improve", _route_improve,
                                  {"edit": "edit", "finalize": "finalize", END: END})
    builder.add_edge("edit", "improve")
    builder.add_edge("finalize", END)
    graph = builder.compile()

    try:
        final = graph.invoke({"target": opening or "", "out_stem": out_stem},
                             config={"recursion_limit": 50})
    except Exception as e:
        # A truncated structured-output reply surfaces as LengthFinishReasonError;
        # show a clean message instead of a raw traceback. Re-raise anything else
        # so genuine bugs still surface.
        if type(e).__name__ == "LengthFinishReasonError" or "length limit" in str(e):
            notify_fn("the model's reply was too long to parse — try a more "
                      "specific target, or run again.")
            return {"aborted": True, "yaml": None, "docx": None, "pdf": None,
                    "ok": None, "problems": []}
        raise
    if final.get("aborted"):
        return {"aborted": True, "yaml": None, "docx": None, "pdf": None,
                "ok": None, "problems": []}
    return {"aborted": False, "yaml": final.get("yaml"), "docx": final.get("docx"),
            "pdf": final.get("pdf"), "ok": final.get("ok"),
            "problems": final.get("problems", [])}
