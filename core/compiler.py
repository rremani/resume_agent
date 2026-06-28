"""
Compiler — raw/ -> career/ wiki.

This is the Karpathy 'compile' step adapted with provenance discipline:
the LLM reads the immutable raw sources and produces a clean, structured,
cross-linked set of wiki pages. Then we GROUND the compiled output against raw
so synthesis can't drift facts (the known 'knowledge drift' failure mode).

Output contract: the LLM returns a JSON object describing all wiki pages:
  {
    "profile": {name, title, contact{...}, summary},
    "skills":  [{category, tags, items}],
    "roles":   [{slug, role, company, start, end, location, context, bullets:[...]}],
    "projects":[{slug, name, role?, when?, summary, bullets:[...], tags:[...]}]
  }
We then write each as a markdown+frontmatter page under career/.
"""

from __future__ import annotations
import json
import re
import yaml

from . import store
from .providers import Provider

NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")

COMPILE_SYSTEM = """You compile a candidate's IMMUTABLE raw career sources into a \
clean, structured career wiki. You are maintaining a knowledge base that compounds \
over time.

RULES:
1. Use ONLY facts present in the raw sources. Do not invent metrics, companies, \
dates, titles, technologies, or achievements.
2. You MAY rephrase for clarity and consistency, deduplicate, and cross-link \
related work — but every number/company/date must come from the raw sources.
3. Organize into: profile, skills (grouped), roles (one per employer), and \
projects (notable standalone projects worth their own page).
4. If raw sources conflict, prefer the most recent and note the conflict in the \
relevant 'context' field.

Return ONE JSON object, no markdown fences, with this exact shape:
{
  "profile": {"name": "...", "title": "...",
              "contact": {"email":"","phone":"","location":"","linkedin":"","github":"","medium":""},
              "summary": "...",
              "education": [{"degree":"","institution":"","start":"","end":"","location":""}]},
  "skills": [{"category":"...","tags":["..."],"items":["..."]}],
  "roles": [{"slug":"company-slug","role":"...","company":"...","start":"MM/YYYY",
             "end":"MM/YYYY or Present","location":"...","context":"",
             "bullets":[{"text":"...","tags":["..."]}]}],
  "projects": [{"slug":"proj-slug","name":"...","role":"","when":"",
                "summary":"...","bullets":[{"text":"...","tags":["..."]}],"tags":["..."]}]
}
Output ONLY the JSON object."""

COMPILE_USER = """=== IMMUTABLE RAW CAREER SOURCES ===
{raw}

Compile these into the career-wiki JSON now."""


def _numbers(blob: str):
    return set(NUM_RE.findall(blob))


def ground_against_raw(compiled: dict, raw_text: str):
    """Flag any number/company in the compiled wiki absent from raw."""
    problems = []
    raw_nums = _numbers(raw_text)
    comp_blob = json.dumps(compiled)
    for n in _numbers(comp_blob):
        if n not in raw_nums:
            problems.append(f"Number not found in raw sources: {n!r}")
    # company check
    raw_low = raw_text.lower()
    for r in compiled.get("roles", []):
        co = (r.get("company") or "").strip()
        if co and co.lower() not in raw_low:
            problems.append(f"Company not found in raw sources: {co!r}")
    return (len(problems) == 0, problems)


def compile_wiki(provider: Provider, *, model: str, allow_web: bool = False):
    """Read raw, ask the LLM to compile, ground it, and write career/ pages."""
    raw_text = store.read_all_raw()
    if not raw_text.strip():
        raise RuntimeError("No raw sources to compile. Bootstrap from a resume first.")

    result = provider.complete(
        COMPILE_SYSTEM,
        COMPILE_USER.format(raw=raw_text),
        model=model, max_tokens=6000, allow_web=allow_web,
    )
    text = result.text.replace("```json", "").replace("```", "").strip()
    try:
        compiled = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Compiler returned invalid JSON: {e}\n---\n{text[:500]}")

    ok, problems = ground_against_raw(compiled, raw_text)

    _write_pages(compiled)
    return {"ok": ok, "problems": problems, "compiled": compiled}


def _write_pages(c: dict):
    # profile.md
    prof = c.get("profile", {})
    store.write_career_file("profile.md",
                            {**{k: prof.get(k) for k in ("name", "title", "contact")},
                             "education": prof.get("education", [])},
                            prof.get("summary", ""))
    # skills.md
    store.write_career_file("skills.md",
                            {"skills": c.get("skills", [])},
                            "Compiled skill clusters.")
    # one page per role
    for r in c.get("roles", []):
        slug = r.get("slug") or store.slugify(f'{r.get("company","")}-{r.get("role","")}')
        fm = {k: r.get(k) for k in ("role", "company", "start", "end", "location", "tags")}
        fm["bullets"] = r.get("bullets", [])
        store.write_career_file(f"roles/{slug}.md", fm, r.get("context", "") or "")
    # one page per project
    for p in c.get("projects", []):
        slug = p.get("slug") or store.slugify(p.get("name", "project"))
        fm = {k: p.get(k) for k in ("name", "role", "when", "tags")}
        fm["bullets"] = p.get("bullets", [])
        store.write_career_file(f"projects/{slug}.md", fm, p.get("summary", "") or "")


# ---- convert compiled wiki -> the YAML schema the renderer expects ------

def wiki_to_resume_yaml() -> dict:
    """Assemble the compiled wiki pages into the flat schema render.py wants.
    This is deterministic — no LLM. It just reads the maintained wiki."""
    import glob, os
    prof_fm, prof_body = store.parse_md(open(os.path.join(store.CAREER, "profile.md")).read())
    skills_fm, _ = store.parse_md(open(os.path.join(store.CAREER, "skills.md")).read())

    roles = []
    for p in sorted(glob.glob(os.path.join(store.ROLES, "*.md"))):
        fm, body = store.parse_md(open(p).read())
        roles.append({
            "role": fm.get("role"), "company": fm.get("company"),
            "start": fm.get("start"), "end": fm.get("end"),
            "location": fm.get("location"), "context": body or None,
            "bullets": fm.get("bullets", []),
        })
    # sort roles by start date desc (MM/YYYY)
    def _key(r):
        try:
            mm, yy = str(r.get("start", "01/1900")).split("/")
            return (int(yy), int(mm))
        except Exception:
            return (0, 0)
    roles.sort(key=_key, reverse=True)

    return {
        "name": prof_fm.get("name"),
        "title": prof_fm.get("title"),
        "contact": prof_fm.get("contact", {}),
        "summary": prof_body or "",
        "skills": skills_fm.get("skills", []),
        "experience": roles,
        "education": prof_fm.get("education", []),
    }
