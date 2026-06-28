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
import subprocess
import sys
import yaml

from .providers import Provider
from . import store, compiler

RENDERER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skill", "render.py")
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")

SYSTEM = """You are a resume-tailoring agent. You receive a candidate's COMPLETE, \
verified career history (the canonical store) and a brief describing a target role. \
Produce ONE tailored resume as YAML.

HARD RULES:
1. You may select, reorder, omit, and freely rephrase bullets and the summary.
2. You may NOT invent or alter facts: metrics/numbers (F1, %, MAE...), company \
names, role titles, dates, institutions, and named technologies must match the \
store exactly. Rewording around a number is fine; changing the number is forbidden.
3. Never add experiences/skills not present in the store.
4. Emit the SAME schema as the store: name, title, contact, summary, skills \
(list of {category, tags, items}), experience (list of {role, company, start, end, \
location, context?, bullets:[{text, tags}]}), education.
5. Tailor hard: lead with the most relevant roles/bullets, sharpen the summary \
toward the target, drop weak bullets to stay focused.

Output ONLY valid YAML — no markdown fences, no commentary."""

USER_TMPL = """=== CANONICAL CAREER STORE (read-only source of truth) ===
{store}

=== TARGET BRIEF ===
{brief}

Produce the tailored resume YAML now."""

NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")


def load_store():
    """The compiled career wiki is the input the tailoring agent reads."""
    return store.read_career_bundle()


def generate_yaml(provider: Provider, *, model: str, brief: str,
                  allow_web: bool = False):
    store_text = load_store()
    result = provider.complete(
        SYSTEM,
        USER_TMPL.format(store=store_text, brief=brief),
        model=model, max_tokens=4000, allow_web=allow_web,
    )
    text = result.text.replace("```yaml", "").replace("```", "").strip()
    return text, result.used_web


# ---- grounding validator ------------------------------------------------

def _numbers(blob):
    return set(NUM_RE.findall(blob))


def validate(generated: dict, store: dict):
    problems = []
    s_nums = _numbers(yaml.safe_dump(store))
    for n in _numbers(yaml.safe_dump(generated)):
        if n not in s_nums:
            problems.append(f"Number not in store: {n!r}")

    s_co = {j.get("company", "") for j in store.get("experience", [])}
    for j in generated.get("experience", []):
        if j.get("company") and j["company"] not in s_co:
            problems.append(f"Company not in store: {j['company']!r}")

    s_roles = {(j.get("company"), j.get("role")) for j in store.get("experience", [])}
    for j in generated.get("experience", []):
        if (j.get("company"), j.get("role")) not in s_roles:
            problems.append(f"Role/company not in store: {(j.get('company'), j.get('role'))}")

    return (len(problems) == 0, problems)


# ---- render skill -------------------------------------------------------

def render(yaml_path: str, out_stem: str):
    """Invoke the deterministic skill. Returns (docx, pdf) paths."""
    out_base = os.path.join(OUT_DIR, out_stem)
    subprocess.run([sys.executable, RENDERER, "--yaml", yaml_path, "--out", out_base],
                   check=True)
    return out_base + ".docx", out_base + ".pdf"


def run_once(provider: Provider, *, model: str, brief: str, allow_web: bool,
             out_stem: str, do_render: bool = True):
    """Full single-shot pipeline: generate -> validate -> (write) -> render."""
    gen_text, used_web = generate_yaml(provider, model=model, brief=brief,
                                       allow_web=allow_web)
    try:
        generated = yaml.safe_load(gen_text)
    except yaml.YAMLError as e:
        raise RuntimeError(f"Agent produced invalid YAML: {e}")

    # Canonical facts = the deterministic assembly of the compiled wiki.
    store_resume = compiler.wiki_to_resume_yaml()
    ok, problems = validate(generated, store_resume)

    os.makedirs(OUT_DIR, exist_ok=True)
    yaml_path = os.path.join(OUT_DIR, f"{out_stem}.yaml")
    with open(yaml_path, "w") as f:
        yaml.safe_dump(generated, f, sort_keys=False, allow_unicode=True, width=100)

    docx = pdf = None
    if do_render:
        docx, pdf = render(yaml_path, out_stem)

    return {"yaml": yaml_path, "docx": docx, "pdf": pdf,
            "ok": ok, "problems": problems, "used_web": used_web}
