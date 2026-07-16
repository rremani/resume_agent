"""
Think mode — a LangGraph JD-driven tailoring workflow.

    START → understand → assess → convo → fill → improve → (done?) ─yes→ finalize → END
                                                    ▲          │
                                                    └── edit ──┘   (feedback → one change)

It tailors the candidate's EXISTING verified experience to a target, planning
for what gets SHORTLISTED — but honestly. Local optimum, not global: it never
fabricates experience to match a JD.

- understand: capture the target (ONE simple question if the brief is thin); load
  the FULL vetted wiki as evidence.
- assess:     ONE call reads the JD + wiki → an Assessment (role/keywords/summary
  angle + each requirement marked covered or a GAP). Optionally web-researches the
  role/market. Surfaces the gaps.
- convo:      for the top must-have GAPS, asks the user (one at a time) whether
  they have REAL experience. Uses only what they affirmatively provide — this
  session only, never written to the wiki, never fabricated.
- fill:       builds the resume from the wiki (+ session-provided facts), tuned to
  the assessment. Requirements the candidate lacks are simply not claimed.
- improve/edit: preview + ONE change per round (surgical).
- finalize:   grounds (wiki-strict; session facts allowed but reported as
  provenance) and renders. No knowledge-base writes.
"""
from __future__ import annotations

import os
import re
import yaml
from typing import Optional
from typing_extensions import TypedDict
from pydantic import BaseModel, Field

from langgraph.graph import StateGraph, START, END
from langchain_core.messages import SystemMessage, HumanMessage

from . import store, compiler, paths, agent as core_agent, search as search_mod

MAX_GAP_QUESTIONS = 3  # don't interrogate — ask about the top few must-have gaps


# ---- resume schema (structured output) ---------------------------------

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


# ---- assessment schema (JD analysis + coverage/gap, one call) -----------

class Requirement(BaseModel):
    name: str
    importance: str = "must"     # "must" | "nice"
    covered: bool = False        # does the candidate's VERIFIED experience evidence it?
    evidence: str = ""           # brief note on the supporting evidence, or "" if a gap


class Assessment(BaseModel):
    """What the target rewards + how the candidate's real experience covers it."""
    role_title: str = ""
    company: str = ""
    seniority: str = ""
    summary_angle: str = ""                      # shortlist-oriented framing
    keywords: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    quality_gaps: list[str] = Field(default_factory=list)  # resume best-practice weaknesses


ASSESS_SYSTEM = """You are a senior technical recruiter and resume coach. Given a \
TARGET (a role description or a full job description) and the candidate's VERIFIED \
EXPERIENCE (their career wiki), do THREE things:

1) Analyze the target. What must a resume DEMONSTRATE to get SHORTLISTED for THIS \
role? Extract role_title, company, seniority, the ATS keywords that matter, and a \
shortlist-oriented summary_angle.
2) List the key requirements (mark each importance "must" or "nice"). For each, \
judge from the VERIFIED EXPERIENCE ONLY whether the candidate COVERS it \
(covered=true, with a short evidence note) or NOT (covered=false → a gap). Do not \
assume coverage that the evidence doesn't show.
3) Review the experience against strong-resume BEST PRACTICES and fill `quality_gaps` \
with the most impactful weaknesses to fix (at most 5). The #1 check: accomplishments \
stated WITHOUT quantified impact — no %, count, scale, or before/after — that \
plausibly HAVE a real number the candidate could supply. Phrase each as a SHORT label \
(≤ 8 words) naming the work and what's missing — e.g. "DSPy insights — no accuracy \
metric", "ReAct chatbot — no latency number". NOT full sentences. Do NOT invent \
metrics — only flag where they're missing.

Be honest: mark real gaps as gaps; don't inflate coverage. Output the structured \
assessment."""


FILL_SYSTEM = """You are an expert resume EDITOR building a resume from the \
candidate's REAL evidence, tuned to a target ASSESSMENT — an editor, never an author.

- The ASSESSMENT lists the target's requirements (with which the candidate COVERS), \
the keywords that matter, and a shortlist-oriented summary angle. Foreground the \
COVERED requirements (most important first), mirror the keywords ONLY where the \
candidate genuinely matches, and shape the summary to the angle.
- Facts come from the VERIFIED EXPERIENCE. If the candidate ALSO provided extra real \
experience this session, you may use it as equally real — but never invent beyond \
what they stated.
- LOCAL OPTIMUM, NOT GLOBAL. On your OWN initiative, never fabricate experience, \
skills, titles, or metrics to match the target — a requirement the candidate lacks is \
simply not claimed; a gap stays a gap. (Exception: if an "ESTIMATES AUTHORIZED" note \
is present, the candidate has asked you to fill in missing NUMBERS — see below.)
- Be concise (aim for 1-2 pages): the most relevant roles get more depth, older or \
less-relevant roles get trimmed to their strongest points. Keep the COMPLETE role \
history — tailor by emphasis, not deletion. Strongest bullet first within each role.
- QUANTIFY. Every real metric in the verified experience MUST appear in the bullet — \
never bury a number in vague words ("measurable gains", "significantly reduced"): if \
the wiki says 90% F1, 80% reduction, or ~300 units, state it. Lead with the \
quantified outcome. By default do NOT invent a metric that isn't in the verified or \
provided facts — leave it qualitative. ONLY if "ESTIMATES AUTHORIZED" is present may \
you add realistic placeholder numbers for missing metrics (keep them modest — the \
candidate will verify them).
- Company names, job titles, dates, and institutions are LOCKED to the verified \
experience — never alter or inflate a title (e.g. "Data Scientist" must not become \
"Lead Data Scientist"). Match them exactly.

Output the structured resume."""


# ---- model builder ------------------------------------------------------

# A full-resume structured-output JSON can exceed 4k tokens; too low a cap
# truncates the JSON mid-object and the parser raises LengthFinishReasonError.
MAX_OUTPUT_TOKENS = 8192


def _build_model(cfg: dict, model_id: str, max_tokens: int = MAX_OUTPUT_TOKENS):
    provider = (cfg.get("provider") or "anthropic").lower()
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model_id, max_tokens=max_tokens)
    if provider == "openrouter":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model_id, max_tokens=max_tokens,
                          base_url="https://openrouter.ai/api/v1",
                          api_key=os.environ.get("OPENROUTER_API_KEY"))
    raise ValueError(f"Unknown provider {provider!r}")


# ---- opening greeting (LLM-authored, background-aware) -----------------

GREET_SYSTEM = """You are a warm, sharp resume-tailoring assistant opening a \
session. Given a short summary of the candidate's background, write a SHORT \
greeting (1-2 sentences) that invites them to tell you the role they're targeting \
— they can paste a job description or describe the role/company. You may \
acknowledge their background at a high level in ONE clause, but do NOT list \
specifics or invent anything, and ask only this one thing. Plain text, no markdown."""


def wiki_summary() -> str:
    """A tiny background summary (roles + skill categories) for the greeting."""
    r = compiler.wiki_to_resume_yaml()
    roles = [j.get("role", "") for j in r.get("experience", [])][:6]
    cats = [g.get("category", "") for g in r.get("skills", [])]
    return ("Roles: " + ", ".join(x for x in roles if x)
            + "\nSkills: " + ", ".join(c for c in cats if c))


def _degenerate(text: str) -> bool:
    """True for a repetition-loop output (weak models sometimes loop forever)."""
    words = text.split()
    return len(words) > 25 and len(set(w.lower() for w in words)) < len(words) * 0.4


def session_greeting(cfg: dict) -> str:
    """LLM-authored opening message, aware of the candidate's background. Hard-capped
    and truncated to 1-2 sentences; returns "" (→ static fallback) on a degenerate
    loop, so a rambling model can never flood the terminal."""
    model = _build_model(cfg, cfg["model"], max_tokens=120)
    msg = model.invoke([SystemMessage(content=GREET_SYSTEM),
                        HumanMessage(content="Candidate background:\n" + wiki_summary()
                                     + "\n\nWrite the opening greeting now.")])
    content = getattr(msg, "content", msg)
    if isinstance(content, list):   # some providers return content parts
        content = " ".join(str(getattr(p, "text", p)) for p in content)
    raw = " ".join(str(content).split())               # collapse whitespace/newlines
    text = " ".join(re.split(r"(?<=[.!?])\s+", raw)[:2]).strip()[:320]   # first 1-2 sentences
    return "" if _degenerate(text) else text


# ---- LLM steps (module-level, testable with a stub model) --------------

def assess_jd(model, target: str, evidence: str) -> Assessment:
    """One call: analyze the target AND mark which requirements the wiki covers."""
    user = [f"=== TARGET (role / job description) ===\n{target}",
            f"=== CANDIDATE'S VERIFIED EXPERIENCE (the only basis for coverage) ===\n{evidence}",
            "Analyze the target and assess coverage now."]
    structured = model.with_structured_output(Assessment)
    return structured.invoke([SystemMessage(content=ASSESS_SYSTEM),
                              HumanMessage(content="\n\n".join(user))])


ESTIMATES_NOTE = ("=== ESTIMATES AUTHORIZED ===\nThe candidate has explicitly asked you "
                  "to fill in MISSING NUMBERS with reasonable estimates and will verify "
                  "them. You MAY add realistic placeholder metrics where a strong bullet "
                  "lacks one — keep them modest and believable. Do NOT invent whole "
                  "experiences, skills, or titles; only fill in missing quantities.")


def fill_resume(model, assessment: dict, evidence: str, provided: Optional[list] = None,
                research: str = "", allow_estimates: bool = False) -> Resume:
    """Build the resume from verified evidence (+ session-provided facts), tuned to
    the assessment. Fabricates numbers ONLY if the candidate authorized estimates."""
    user = ["=== TARGET ASSESSMENT (what gets shortlisted; requirements + coverage) ===\n"
            + yaml.safe_dump(assessment, sort_keys=False, allow_unicode=True),
            "=== CANDIDATE'S VERIFIED EXPERIENCE (primary source of facts) ===\n" + evidence]
    if provided:
        user.append("=== ALSO PROVIDED BY THE CANDIDATE THIS SESSION (real; use where "
                    "relevant) ===\n" + "\n".join(f"- {p}" for p in provided))
    if research:
        user.append("=== MARKET CONTEXT (framing/keywords only — NOT resume facts) ===\n"
                    + research)
    if allow_estimates:
        user.append(ESTIMATES_NOTE)
    user.append("Build the tailored resume now.")
    structured = model.with_structured_output(Resume)
    return structured.invoke([SystemMessage(content=FILL_SYSTEM),
                              HumanMessage(content="\n\n".join(user))])


# ---- scoped section edits (only ever touch ONE section) ----------------

# Each edit returns just the revised section, so an edit can NEVER drop other
# roles/bullets — the structural fix for whole-resume regeneration wiping content.

class Bullets(BaseModel):
    bullets: list[Bullet] = Field(default_factory=list)


class SummaryOut(BaseModel):
    summary: str = ""


class SkillsOut(BaseModel):
    skills: list[SkillGroup] = Field(default_factory=list)


EDIT_SECTION_SYSTEM = """You are editing ONE section of a resume — an editor, never \
an author. Apply the user's requested change and return the full revised section. \
Keep every bullet/fact you were NOT asked to change exactly as-is. Company names, job \
titles, dates and institutions are LOCKED — never alter or inflate them. By default do \
NOT fabricate metrics/experience; if it isn't supported, don't add it. EXCEPTION: if an \
"ESTIMATES AUTHORIZED" note is present, the user asked you to fill missing NUMBERS — you \
MAY add realistic placeholder metrics they will verify. Be concise and recruiter-grade \
— strong action verbs, impact-first, no hedging tails."""


def _edit_user(parts: list, allow_estimates: bool) -> str:
    if allow_estimates:
        parts = parts[:-1] + [ESTIMATES_NOTE, parts[-1]]
    return "\n\n".join(parts)


def edit_role_bullets(model, role: dict, feedback: str, evidence: str,
                      allow_estimates: bool = False) -> list:
    cur = "\n".join(f"{i}. {_bullet_text(b)}"
                    for i, b in enumerate(role.get("bullets", []), 1))
    parts = [f"=== SECTION: {role.get('role','')} — {role.get('company','')} ===",
             "=== CURRENT BULLETS (numbered) ===\n" + cur,
             f"=== VERIFIED EXPERIENCE (the only source of facts) ===\n{evidence}",
             f"=== CHANGE REQUESTED ===\n{feedback}",
             "Return the full revised bullet list for THIS role only."]
    out = model.with_structured_output(Bullets).invoke(
        [SystemMessage(content=EDIT_SECTION_SYSTEM),
         HumanMessage(content=_edit_user(parts, allow_estimates))])
    return [b.model_dump() for b in out.bullets]


def edit_summary(model, draft: dict, feedback: str, evidence: str,
                 allow_estimates: bool = False) -> str:
    parts = [f"=== CURRENT SUMMARY ===\n{draft.get('summary','')}",
             f"=== VERIFIED EXPERIENCE ===\n{evidence}",
             f"=== CHANGE REQUESTED ===\n{feedback}",
             "Return the revised summary."]
    return model.with_structured_output(SummaryOut).invoke(
        [SystemMessage(content=EDIT_SECTION_SYSTEM),
         HumanMessage(content=_edit_user(parts, allow_estimates))]).summary


def edit_skills(model, draft: dict, feedback: str, evidence: str,
                allow_estimates: bool = False) -> list:
    parts = ["=== CURRENT SKILLS ===\n"
             + yaml.safe_dump(draft.get("skills", []), allow_unicode=True),
             f"=== VERIFIED EXPERIENCE ===\n{evidence}",
             f"=== CHANGE REQUESTED ===\n{feedback}",
             "Return the revised skills."]
    out = model.with_structured_output(SkillsOut).invoke(
        [SystemMessage(content=EDIT_SECTION_SYSTEM),
         HumanMessage(content=_edit_user(parts, allow_estimates))])
    return [s.model_dump() for s in out.skills]


# ---- helpers -----------------------------------------------------------

def gaps_of(assessment: dict, must_only: bool = False) -> list:
    """Requirements the verified wiki does not cover."""
    reqs = (assessment or {}).get("requirements", []) or []
    out = [r for r in reqs if not r.get("covered")]
    if must_only:
        out = [r for r in out if (r.get("importance") or "must") == "must"]
    return out


def strategy_of(assessment: dict) -> dict:
    """A small {title, themes} view for the preview/strategy block."""
    a = assessment or {}
    covered = [r for r in a.get("requirements", []) if r.get("covered")]
    return {"title": a.get("role_title") or "your target",
            "themes": [{"name": r.get("name", "")} for r in covered[:5]]}


# The candidate can EXPLICITLY authorize the agent to fill in missing numbers
# ("you decide", "fill them in", "as per your understanding", "I'll verify").
_ESTIMATE_OK = re.compile(
    r"\b(fill (it|them|these|in|out)|you (can |could )?(fill|decide|choose|figure|"
    r"add|put)|as per your|make (it|them|one) up|your understanding|whatever you "
    r"(think|feel|want|like)|wherever you (feel|think|see)|approximate|estimate|"
    r"your (best )?guess|guess (it|them)|reasonable numbers?)\b", re.I)


def authorizes_estimates(text: str) -> bool:
    """True when the user explicitly asks the agent to invent/estimate numbers."""
    return bool(_ESTIMATE_OK.search(text or ""))


def lock_identity(draft: dict, wiki: dict) -> dict:
    """Copy IMMUTABLE identity from the wiki so tailoring can't alter it: name,
    contact, education, and each role's title/company/dates/location. Summary,
    skills, and bullets stay tailored. Structurally prevents unsolicited title
    inflation (e.g. 'Data Scientist' → 'Lead Data Scientist')."""
    import copy
    draft = copy.deepcopy(draft)
    for k in ("name", "contact", "education"):
        if wiki.get(k) is not None:
            draft[k] = wiki[k]
    wiki_roles = wiki.get("experience", []) or []
    by_company = {}
    for r in wiki_roles:
        by_company.setdefault(core_agent._norm(r.get("company", "")), r)
    for i, j in enumerate(draft.get("experience", []) or []):
        m = by_company.get(core_agent._norm(j.get("company", "")))
        if m is None and i < len(wiki_roles):
            m = wiki_roles[i]          # fall back to order if the company was altered
        if m:
            for k in ("role", "company", "start", "end", "location"):
                if m.get(k) is not None:
                    j[k] = m.get(k)
    return draft


def _bullet_text(b) -> str:
    return b.get("text", "") if isinstance(b, dict) else str(b)


def strategy_text(assessment: dict) -> str:
    strat = strategy_of(assessment or {})
    lines = ["Tailoring toward: " + strat["title"]]
    if strat["themes"]:
        lines.append("Leading with " + " · ".join(t["name"] for t in strat["themes"][:5]))
    return "\n".join(lines)


# -- sections: the resume as reviewable, one-at-a-time units --

def sections(draft: dict) -> list:
    """Section descriptors: ('summary',), ('exp', i), ('skills',)."""
    secs = [("summary",)]
    for i in range(len(draft.get("experience", []))):
        secs.append(("exp", i))
    if draft.get("skills"):
        secs.append(("skills",))
    return secs


def section_label(draft: dict, sec: tuple) -> str:
    if sec[0] == "summary":
        return "Summary"
    if sec[0] == "skills":
        return "Skills"
    j = draft["experience"][sec[1]]
    return f'{j.get("role","")} — {j.get("company","")}'


def section_menu(draft: dict) -> str:
    lines = []
    for n, sec in enumerate(sections(draft), 1):
        label = section_label(draft, sec)
        if sec[0] == "exp":
            k = len(draft["experience"][sec[1]].get("bullets", []))
            label += f"  ({k} bullet{'s' if k != 1 else ''})"
        lines.append(f"  {n}. {label}")
    return "\n".join(lines)


def section_view(draft: dict, sec: tuple) -> str:
    if sec[0] == "summary":
        return draft.get("summary", "") or "(no summary yet)"
    if sec[0] == "skills":
        return "\n".join(f"• {g.get('category','')}: " + ", ".join(g.get("items", []))
                         for g in draft.get("skills", []))
    j = draft["experience"][sec[1]]
    lines = [f'{j.get("role","")} — {j.get("company","")}  '
             f'({j.get("start","")}–{j.get("end","")})']
    for i, b in enumerate(j.get("bullets", []), 1):
        lines.append(f"  {i}. {_bullet_text(b)}")
    return "\n".join(lines)


def parse_drops(feedback: str):
    """A leading drop/remove/delete + numbers → deterministic delete (no LLM)."""
    if not re.match(r"^\s*(drop|remove|delete)\b", feedback, re.I):
        return None
    nums = sorted({int(x) for x in re.findall(r"#?(\d+)", feedback)})
    return nums or None


def apply_section_edit(model, draft: dict, sec: tuple, feedback: str,
                       evidence: str, notify=lambda _t: None,
                       allow_estimates: bool = False) -> dict:
    """Apply the change to ONE section and return a new draft. Deterministic for
    drop/remove; LLM (scoped to the section) for wording. Other sections are never
    touched, so nothing else can be lost."""
    import copy
    draft = copy.deepcopy(draft)
    if sec[0] == "exp":
        role = draft["experience"][sec[1]]
        drops = parse_drops(feedback)
        if drops is not None:
            notify("removing bullets…")
            role["bullets"] = [b for i, b in enumerate(role.get("bullets", []), 1)
                               if i not in drops]
        else:
            notify("revising this section…")
            role["bullets"] = edit_role_bullets(model, role, feedback, evidence, allow_estimates)
    elif sec[0] == "summary":
        notify("revising the summary…")
        draft["summary"] = edit_summary(model, draft, feedback, evidence, allow_estimates)
    elif sec[0] == "skills":
        notify("revising skills…")
        draft["skills"] = edit_skills(model, draft, feedback, evidence, allow_estimates)
    return draft


# ---- graph state + routers ---------------------------------------------

class State(TypedDict, total=False):
    target: str
    evidence: str
    assessment: dict
    research: str
    provided: list       # user-attested facts gathered this session (gap convo)
    estimates: bool      # candidate authorized filling missing numbers → flag to verify
    draft: dict
    aborted: bool
    out_stem: str
    ok: bool
    problems: list
    yaml: str
    docx: str
    pdf: str


def _route_understand(state: State):
    return END if state.get("aborted") else "assess"


def _route_convo(state: State):
    return END if state.get("aborted") else "fill"


def _route_refine(state: State):
    return END if state.get("aborted") else "finalize"


# ---- orchestrator -------------------------------------------------------

def run_think(cfg: dict, *, opening: str, out_stem: str, ask_fn, say_fn,
              notify_fn, render_fn, allow_web: bool = True, on_section=None) -> dict:
    """Run the JD-driven tailoring workflow → grounded resume → render.

    on_section(title, body) is an optional render hook (the inline TUI) for the
    section menu / a single section as a titled panel; plain mode falls back to
    say_fn text."""
    _cache = {}

    def emit(title: str, body: str):
        if on_section:
            on_section(title, body)
        else:
            say_fn(f"— {title} —\n{body}")

    def model():
        if "m" not in _cache:
            _cache["m"] = _build_model(cfg, cfg["model"])
        return _cache["m"]

    def understand(state: State) -> dict:
        target = (state.get("target") or "").strip()
        if not target:   # only ask when nothing was given (e.g. bare /think)
            say_fn("What role are you targeting? Paste a job description, or describe it.")
            ans = (ask_fn() or "").strip()
            if ans.lower() in ("quit", "exit", "cancel"):
                return {"aborted": True}
            target = ans
        notify_fn("loading your verified experience…")
        return {"target": target, "evidence": store.read_career_bundle(),
                "aborted": False}

    def assess(state: State) -> dict:
        notify_fn("analyzing the target against your experience…")
        a = assess_jd(model(), state["target"], state["evidence"]).model_dump()
        # Web-research the role/market (any target, not just short ones) — uses the
        # extracted role+company so a pasted JD gets a sensible query.
        research = ""
        if allow_web:
            tool = search_mod.from_config(cfg)
            if tool is not None:
                q = (f'{a.get("role_title","")} {a.get("company","")}'.strip()
                     or state["target"][:100])
                notify_fn("researching the role & market…")
                try:
                    research = search_mod.format_results(tool.search(q, max_results=3))
                except search_mod.SearchError:
                    research = ""
        # One short line on what the gap analysis found — JD coverage; the quality
        # gaps are handled by the convo (not dumped twice).
        all_gaps = gaps_of(a)
        must_gaps = gaps_of(a, must_only=True)
        quality = a.get("quality_gaps") or []
        if all_gaps:
            must_set = {g["name"] for g in must_gaps}
            others = [g["name"] for g in all_gaps if g["name"] not in must_set]
            parts = []
            if must_gaps:
                parts.append("important: " + ", ".join(g["name"] for g in must_gaps))
            if others:
                parts.append("also: " + ", ".join(others[:5]))
            say_fn("A few things look thin vs the role — " + "; ".join(parts) + ".")
        elif not quality:
            say_fn("Your experience covers the key requirements and the bullets read "
                   "strong — let's review.")
        return {"assessment": a, "research": research}

    def convo(state: State) -> dict:
        a = state["assessment"]
        provided, estimates = [], False
        _NO = ("no", "n", "skip", "none", "nope", "")
        # 1) JD must-have gaps — honest, one at a time, capped. Guard bare answers.
        for g in gaps_of(a, must_only=True)[:MAX_GAP_QUESTIONS]:
            say_fn(f"Do you have real experience with \"{g['name']}\"? "
                   "Describe briefly, or say 'no' / 'skip'.")
            ans = (ask_fn() or "").strip()
            low = ans.lower()
            if low in ("quit", "exit", "cancel"):
                return {"aborted": True}
            if low in _NO:
                continue
            if low in ("yes", "yeah", "yep", "y", "sure") or len(ans.split()) < 3:
                say_fn(f"Got it — briefly, what did you build or do with {g['name']}? "
                       "(or 'skip' to leave it out)")
                ans = (ask_fn() or "").strip()
                if ans.lower() in _NO + ("quit", "exit", "cancel"):
                    continue
            provided.append(f"{g['name']}: {ans}")
        # 2) Quality gaps — one short ask; the user may share real numbers OR
        #    explicitly authorize estimates ("you decide / fill them in").
        quality = a.get("quality_gaps") or []
        if quality:
            say_fn("A few bullets would land harder with real numbers ("
                   + " · ".join(quality[:5]) + "). Share any you have, tell me to fill "
                   "them in, or 'skip'.")
            ans = (ask_fn() or "").strip()
            low = ans.lower()
            if low in ("quit", "exit", "cancel"):
                return {"aborted": True}
            if authorizes_estimates(ans):
                estimates = True
                say_fn("Okay — I'll add reasonable estimates and flag them so you can "
                       "verify before sending.")
            elif low not in _NO:
                provided.append("Metrics the candidate confirms: " + ans)
        return {"provided": provided, "estimates": estimates}

    def fill(state: State) -> dict:
        notify_fn("building your best-matching resume…")
        resume = fill_resume(model(), state["assessment"], state["evidence"],
                             provided=state.get("provided"), research=state.get("research", ""),
                             allow_estimates=bool(state.get("estimates")))
        # Lock identity (name/contact/education/role titles/dates) to the wiki so a
        # title can never be inflated on the agent's own initiative.
        draft = lock_identity(resume.model_dump(), compiler.wiki_to_resume_yaml())
        return {"draft": draft}

    def refine(state: State) -> dict:
        """Section navigator: pick a section → view it → converse to change it or
        go back → 'done'. Edits are SCOPED to the open section, so nothing else is
        ever lost."""
        draft = state["draft"]
        estimates = bool(state.get("estimates"))
        evidence = state["evidence"]
        if state.get("provided"):
            evidence += "\n=== PROVIDED THIS SESSION ===\n" + "\n".join(state["provided"])
        emit("strategy", strategy_text(state.get("assessment") or {}))
        while True:
            emit("sections", section_menu(draft)
                 + "\n\nType a number to open a section · 'done' to generate · 'quit' to abort.")
            choice = (ask_fn() or "").strip().lower()
            if choice in ("quit", "exit", "cancel"):
                return {"aborted": True}
            if choice in ("done", "go", "ok", "generate", ""):
                return {"draft": draft, "estimates": estimates}
            secs = sections(draft)
            if not choice.isdigit() or not (1 <= int(choice) <= len(secs)):
                say_fn("Please type a section number, 'done', or 'quit'.")
                continue
            sec = secs[int(choice) - 1]
            while True:   # conversation on this one section
                emit(section_label(draft, sec), section_view(draft, sec))
                say_fn("Tell me what to change here (e.g. 'drop 2', 'tighten 1', "
                       "'reword 3 to emphasise scale') — or 'back' / 'done' / 'quit'.")
                fb = (ask_fn() or "").strip()
                low = fb.lower()
                if low in ("back", "b", ""):
                    break
                if low in ("quit", "exit", "cancel"):
                    return {"aborted": True}
                if low in ("done", "generate"):
                    return {"draft": draft, "estimates": estimates}
                ok_est = authorizes_estimates(fb)
                estimates = estimates or ok_est
                draft = apply_section_edit(model(), draft, sec, fb, evidence, notify_fn,
                                           allow_estimates=ok_est)

    def finalize(state: State) -> dict:
        from skill.render import _sanitize
        generated = _sanitize(state["draft"])   # strip the em-dash tell before persisting
        store_resume = compiler.wiki_to_resume_yaml()
        provided = state.get("provided") or []
        # Ground numbers/companies/roles against the wiki bundle PLUS the user's
        # session-attested facts (real, so not false-flagged) — but surface the
        # session facts separately as provenance so the user knows what to verify.
        evidence = state.get("evidence", "") + "\n" + "\n".join(provided)
        ok, problems = core_agent.validate(generated, store_resume, evidence=evidence)
        out_dir = paths.output_dir()
        os.makedirs(out_dir, exist_ok=True)
        yaml_path = os.path.join(out_dir, f"{state['out_stem']}.yaml")
        with open(yaml_path, "w") as f:
            yaml.safe_dump(generated, f, sort_keys=False, allow_unicode=True, width=100)
        docx, pdf = render_fn(generated, state["out_stem"])
        return {"ok": ok, "problems": problems, "provided": provided,
                "estimates": bool(state.get("estimates")),
                "yaml": yaml_path, "docx": docx, "pdf": pdf}

    builder = StateGraph(State)
    builder.add_node("understand", understand)
    builder.add_node("assess", assess)
    builder.add_node("convo", convo)
    builder.add_node("fill", fill)
    builder.add_node("refine", refine)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "understand")
    builder.add_conditional_edges("understand", _route_understand,
                                  {"assess": "assess", END: END})
    builder.add_edge("assess", "convo")
    builder.add_conditional_edges("convo", _route_convo, {"fill": "fill", END: END})
    builder.add_edge("fill", "refine")
    builder.add_conditional_edges("refine", _route_refine,
                                  {"finalize": "finalize", END: END})
    builder.add_edge("finalize", END)
    graph = builder.compile()

    try:
        final = graph.invoke({"target": opening or "", "out_stem": out_stem},
                             config={"recursion_limit": 50})
    except Exception as e:
        if type(e).__name__ == "LengthFinishReasonError" or "length limit" in str(e):
            notify_fn("the model's reply was too long to parse — try a more "
                      "specific target, or run again.")
            return {"aborted": True, "yaml": None, "docx": None, "pdf": None,
                    "ok": None, "problems": [], "provided": []}
        raise
    if final.get("aborted"):
        return {"aborted": True, "yaml": None, "docx": None, "pdf": None,
                "ok": None, "problems": [], "provided": []}
    return {"aborted": False, "yaml": final.get("yaml"), "docx": final.get("docx"),
            "pdf": final.get("pdf"), "ok": final.get("ok"),
            "problems": final.get("problems", []), "provided": final.get("provided", []),
            "estimates": final.get("estimates", False)}
