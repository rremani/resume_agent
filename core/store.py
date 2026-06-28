"""
Wiki-style career store (Karpathy LLM-wiki pattern, adapted for resumes).

Two layers:
  raw/      immutable, append-only source of truth. Never edited after writing.
            Each fact (original resume, a project, a cert) is one dated md file.
  career/   compiled wiki the agent maintains: markdown + YAML frontmatter,
            one file per role / project, plus skills.md and profile.md.
            The resume generator reads from here.

Integrity rules (enforced elsewhere):
  - raw/ is append-only.
  - every metric/company/date in career/ must trace to something in raw/
    (grounding runs on the COMPILE step, not just at render time).

Each markdown file is: a YAML frontmatter block (--- ... ---) of structured
fields, followed by free prose. This keeps machine-readable rigor AND human
readability — and is exactly the format the wiki pattern uses.
"""

from __future__ import annotations
import os
import re
import datetime
import glob
import yaml

BASE = os.path.dirname(os.path.dirname(__file__))
RAW = os.path.join(BASE, "raw")
CAREER = os.path.join(BASE, "career")
ROLES = os.path.join(CAREER, "roles")
PROJECTS = os.path.join(CAREER, "projects")


def _ensure_dirs():
    for d in (RAW, CAREER, ROLES, PROJECTS):
        os.makedirs(d, exist_ok=True)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "entry"


# ---- frontmatter parsing/serialization ---------------------------------

FM_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)

def parse_md(text: str):
    """Return (frontmatter_dict, body_str)."""
    m = FM_RE.match(text)
    if not m:
        return {}, text
    fm = yaml.safe_load(m.group(1)) or {}
    return fm, m.group(2).strip()

def build_md(frontmatter: dict, body: str = "") -> str:
    fm = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{fm}\n---\n\n{body.strip()}\n"


# ---- raw layer (append-only) -------------------------------------------

def add_raw(kind: str, title: str, content: str, extra: dict | None = None) -> str:
    """Write an immutable raw entry. kind in {resume, project, cert, note}.
    Returns the path. Never overwrites — appends a counter if needed."""
    _ensure_dirs()
    date = datetime.date.today().isoformat()
    base = f"{date}-{kind}-{slugify(title)}"
    path = os.path.join(RAW, base + ".md")
    n = 2
    while os.path.exists(path):
        path = os.path.join(RAW, f"{base}-{n}.md")
        n += 1
    fm = {"kind": kind, "title": title, "captured": date}
    if extra:
        fm.update(extra)
    with open(path, "w") as f:
        f.write(build_md(fm, content))
    return path

def read_all_raw() -> str:
    """Concatenate every raw entry — the full source of truth for grounding."""
    _ensure_dirs()
    chunks = []
    for p in sorted(glob.glob(os.path.join(RAW, "*.md"))):
        chunks.append(f"### RAW SOURCE: {os.path.basename(p)}\n{open(p).read()}")
    return "\n\n".join(chunks)

def raw_is_empty() -> bool:
    _ensure_dirs()
    return not glob.glob(os.path.join(RAW, "*.md"))


# ---- career (compiled) layer -------------------------------------------

def write_career_file(relpath: str, frontmatter: dict, body: str = ""):
    """Write/overwrite a compiled wiki page (agent-maintained)."""
    _ensure_dirs()
    path = os.path.join(CAREER, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(build_md(frontmatter, body))
    return path

def read_career_bundle() -> str:
    """Concatenate the compiled wiki — what the resume generator reads."""
    _ensure_dirs()
    chunks = []
    for p in sorted(glob.glob(os.path.join(CAREER, "**", "*.md"), recursive=True)):
        rel = os.path.relpath(p, CAREER)
        chunks.append(f"### WIKI PAGE: {rel}\n{open(p).read()}")
    return "\n\n".join(chunks)

def career_is_empty() -> bool:
    _ensure_dirs()
    return not glob.glob(os.path.join(CAREER, "**", "*.md"), recursive=True)
