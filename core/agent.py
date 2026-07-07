"""
Agent core — provider-agnostic.

Responsibilities:
  - Build the tailoring prompt from the canonical store + a brief.
  - Call the configured Provider to produce a tailored resume YAML.
  - Ground-check the result against the store (facts must trace back).
  - Invoke the deterministic render skill to emit PDF + DOCX.

This module knows nothing about Anthropic vs OpenRouter, nothing about the CLI,
and nothing about modes beyond the two flags it's handed (model, allow_web).
That isolation is what lets it be reused inside a larger agent later.
"""

from __future__ import annotations
import os
import re
import yaml

from .providers import Provider
from . import store, compiler, paths

SYSTEM = """You are an elite resume writer for FAANG/top-tech roles. You receive a \
candidate's COMPLETE, verified career history (the canonical store) and a brief \
describing a target role. Produce ONE sharply tailored resume as YAML.

HARD RULES (never break — these protect the candidate from fabrication):
1. You may select, reorder, omit, and freely REWRITE bullets and the summary.
2. You may NOT invent or alter facts: metrics/numbers (F1, %, MAE...), company \
names, role titles, dates, institutions, and named technologies must match the \
store exactly. Rewording around a number is fine; changing/adding a number is forbidden.
3. Never add experiences/skills not present in the store. Keep the COMPLETE role \
history (omitting a job creates suspicious gaps) — tailor by emphasis, not deletion.
4. Emit the SAME schema as the store: name, title, contact, summary, skills \
(list of {category, tags, items}), experience (list of {role, company, start, end, \
location, context?, bullets:[{text, tags}]}), education.

WRITING QUALITY (this is what makes it great, not generic):
- Lead every bullet with a strong past-tense action verb (Architected, Shipped, \
Cut, Scaled, Drove) — never "Responsible for" / "Worked on".
- Use the impact-first shape: accomplished [X] measured by [Y] by doing [Z]. Put \
the quantified result early in the bullet.
- Mirror the target brief's language and keywords where the candidate's real \
experience genuinely matches — surface the most relevant work first.
- Sharpen the summary into 2-3 punchy lines aimed squarely at the target role.
- Cut filler and weak bullets; tighten to the strongest, most relevant evidence. \
Prefer specific over vague. Aim for a focused 1-2 page resume.

Output ONLY valid YAML — no markdown fences, no commentary."""

USER_TMPL = """=== CANONICAL CAREER STORE (read-only source of truth) ===
{store}
{research}
=== TARGET BRIEF ===
{brief}

Produce the tailored resume YAML now."""

RESEARCH_TMPL = """
=== RESEARCH CONTEXT (about the target; for TAILORING GUIDANCE ONLY) ===
Use this to decide what to emphasize and how to frame the summary. It is NOT a
source of resume facts — every metric/company/date must still come from the
store above.
{research}
"""

NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")


def load_store():
    """The compiled career wiki is the input the tailoring agent reads."""
    return store.read_career_bundle()


def gather_research(search_tool, brief: str, *, allow_web: bool,
                    max_results: int = 3) -> str:
    """Run the explicit search tool and format results as prompt context.

    The single gate for web usage: returns "" (and makes NO call) unless web is
    allowed AND a tool is configured AND there's a brief to research. `fast`
    mode passes allow_web=False, so it can never reach the network here."""
    if not allow_web or search_tool is None or not (brief or "").strip():
        return ""
    from . import search as search_mod
    try:
        results = search_tool.search(brief, max_results=max_results)
    except search_mod.SearchError:
        return ""  # research is best-effort; never block resume generation
    return search_mod.format_results(results)


def generate_yaml(provider: Provider, *, model: str, brief: str,
                  allow_web: bool = False, research: str = ""):
    store_text = load_store()
    research_block = RESEARCH_TMPL.format(research=research) if research else ""
    result = provider.complete(
        SYSTEM,
        USER_TMPL.format(store=store_text, brief=brief, research=research_block),
        model=model, max_tokens=4000, allow_web=allow_web,
    )
    text = result.text.replace("```yaml", "").replace("```", "").strip()
    return text, result.used_web


# ---- grounding validator ------------------------------------------------

def _numbers(blob):
    return set(NUM_RE.findall(blob))


def _norm(text):
    """Normalize dashes so em/en-dash retitling isn't a false grounding flag."""
    return (text or "").replace("—", "-").replace("–", "-").strip()


def validate(generated: dict, store: dict, evidence: str = ""):
    """Ground the generated resume against the candidate's verified material.

    Numbers are validated against the SAME evidence the tailoring agent reads —
    the full career bundle (`store.read_career_bundle()`), which includes project
    pages. The assembled resume YAML (`store`) drops project-page metrics, so
    validating numbers against it alone false-flags any real metric the agent
    legitimately pulled from a project (e.g. "~300 units"). Pass `evidence` to
    close that gap; company/role facts still validate against the assembled store.
    """
    problems = []
    # allow_unicode so chars like the em-dash aren't escaped to — and then
    # mis-read as the "number" 2014 by the digit regex.
    s_nums = _numbers(yaml.safe_dump(store, allow_unicode=True) + "\n" + (evidence or ""))
    for n in _numbers(yaml.safe_dump(generated, allow_unicode=True)):
        if n not in s_nums:
            problems.append(f"Number not in store: {n!r}")

    s_co = {_norm(j.get("company", "")) for j in store.get("experience", [])}
    for j in generated.get("experience", []):
        if j.get("company") and _norm(j["company"]) not in s_co:
            problems.append(f"Company not in store: {j['company']!r}")

    s_roles = {(_norm(j.get("company")), _norm(j.get("role")))
               for j in store.get("experience", [])}
    for j in generated.get("experience", []):
        if (_norm(j.get("company")), _norm(j.get("role"))) not in s_roles:
            problems.append(f"Role/company not in store: {(j.get('company'), j.get('role'))}")

    return (len(problems) == 0, problems)


# ---- render skill -------------------------------------------------------

def render(data: dict, out_stem: str):
    """Invoke the deterministic render skill in-process. Returns (docx, pdf).

    Imported and called directly (not shelled out to a script): this keeps the
    pipeline working inside an installed package or a frozen single-file binary,
    where there is no separate python interpreter or on-disk render.py to spawn.
    The skill stays self-contained — it imports nothing from core."""
    from skill import render as render_skill
    out_base = os.path.join(paths.output_dir(), out_stem)
    return render_skill.build(data, None, out_base)


def run_once(provider: Provider, *, model: str, brief: str, allow_web: bool,
             out_stem: str, do_render: bool = True,
             search_tool=None, search_max_results: int = 3):
    """Full single-shot pipeline: research -> generate -> validate -> (write) -> render."""
    research = gather_research(search_tool, brief, allow_web=allow_web,
                               max_results=search_max_results)
    gen_text, used_web = generate_yaml(provider, model=model, brief=brief,
                                       allow_web=allow_web, research=research)
    try:
        generated = yaml.safe_load(gen_text)
    except yaml.YAMLError as e:
        raise RuntimeError(f"Agent produced invalid YAML: {e}")
    from skill.render import _sanitize
    generated = _sanitize(generated)   # strip the em-dash tell before persisting

    # Canonical facts = the deterministic assembly of the compiled wiki; numbers
    # are also allowed from the full bundle (project pages) the agent read.
    store_resume = compiler.wiki_to_resume_yaml()
    ok, problems = validate(generated, store_resume, evidence=load_store())

    out_dir = paths.output_dir()
    os.makedirs(out_dir, exist_ok=True)
    yaml_path = os.path.join(out_dir, f"{out_stem}.yaml")
    with open(yaml_path, "w") as f:
        yaml.safe_dump(generated, f, sort_keys=False, allow_unicode=True, width=100)

    docx = pdf = None
    if do_render:
        docx, pdf = render(generated, out_stem)

    return {"yaml": yaml_path, "docx": docx, "pdf": pdf,
            "ok": ok, "problems": problems, "used_web": used_web,
            "used_search": bool(research)}
