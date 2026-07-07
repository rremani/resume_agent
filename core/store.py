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
import json
import hashlib
import datetime
import glob
import yaml

from . import paths

# Backwards-friendly module-level accessors. Data lives under a per-user home
# (see core/paths.py), resolved fresh each call so installed/frozen builds and
# tests can relocate it via $RESUME_AGENT_HOME.
def _ensure_dirs():
    paths.ensure_dirs()


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
    path = os.path.join(paths.raw_dir(), base + ".md")
    n = 2
    while os.path.exists(path):
        path = os.path.join(paths.raw_dir(), f"{base}-{n}.md")
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
    for p in sorted(glob.glob(os.path.join(paths.raw_dir(), "*.md"))):
        chunks.append(f"### RAW SOURCE: {os.path.basename(p)}\n{open(p).read()}")
    return "\n\n".join(chunks)

def raw_is_empty() -> bool:
    _ensure_dirs()
    return not glob.glob(os.path.join(paths.raw_dir(), "*.md"))


# ---- career (compiled) layer -------------------------------------------

def write_career_file(relpath: str, frontmatter: dict, body: str = ""):
    """Write/overwrite a compiled wiki page (agent-maintained)."""
    _ensure_dirs()
    path = os.path.join(paths.career_dir(), relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(build_md(frontmatter, body))
    return path

def read_career_bundle() -> str:
    """Concatenate the compiled wiki — what the resume generator reads."""
    _ensure_dirs()
    chunks = []
    career = paths.career_dir()
    for p in sorted(glob.glob(os.path.join(career, "**", "*.md"), recursive=True)):
        rel = os.path.relpath(p, career)
        chunks.append(f"### WIKI PAGE: {rel}\n{open(p).read()}")
    return "\n\n".join(chunks)

def career_is_empty() -> bool:
    _ensure_dirs()
    return not glob.glob(os.path.join(paths.career_dir(), "**", "*.md"), recursive=True)


# ---- raw → wiki staleness (change detection) ---------------------------
#
# The wiki is a compiled view of raw/. If a user hand-edits raw/, the wiki
# silently goes stale. We record a manifest of each raw file's sha256 (content
# hash, authoritative) + mtime (informational) at compile time; staleness is
# any divergence between raw/ now and that manifest.

def raw_fingerprint() -> dict:
    """{filename: {sha256, mtime}} for every current raw/ entry."""
    _ensure_dirs()
    fp = {}
    for p in sorted(glob.glob(os.path.join(paths.raw_dir(), "*.md"))):
        with open(p, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        fp[os.path.basename(p)] = {"sha256": digest, "mtime": os.path.getmtime(p)}
    return fp


def write_manifest() -> str:
    """Persist the current raw fingerprint — 'what the wiki was built from'.
    Called at the end of every compile (bootstrap / add / recompile)."""
    _ensure_dirs()
    path = paths.manifest_path()
    with open(path, "w") as f:
        json.dump({"version": 1, "files": raw_fingerprint()}, f, indent=2)
    return path


def read_manifest() -> dict | None:
    path = paths.manifest_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def raw_changes() -> dict:
    """Diff raw/ now against the manifest. Returns added/removed/modified
    filename lists plus has_manifest. sha256 (not mtime) decides 'modified'."""
    current = raw_fingerprint()
    man = read_manifest()
    prev = (man or {}).get("files", {})
    cur_names, prev_names = set(current), set(prev)
    modified = sorted(n for n in (cur_names & prev_names)
                      if current[n]["sha256"] != prev[n].get("sha256"))
    return {
        "added": sorted(cur_names - prev_names),
        "removed": sorted(prev_names - cur_names),
        "modified": modified,
        "has_manifest": man is not None,
    }


def wiki_is_stale() -> bool:
    """True if raw/ has changed since the wiki was last compiled (or if there
    is no manifest yet to prove freshness)."""
    if read_manifest() is None:
        return True
    c = raw_changes()
    return bool(c["added"] or c["removed"] or c["modified"])


# ---- wiki retrieval (browse/search) ------------------------------------
#
# Backs the think-mode research agent's LIST / FIND / READ tools: it searches
# and reads its own career wiki to surface the most relevant material for a
# target role, instead of being handed the whole concatenated blob.

def _page_kind(slug: str) -> str:
    if slug == "profile":
        return "profile"
    if slug == "skills":
        return "skills"
    if slug.startswith("roles/"):
        return "role"
    if slug.startswith("projects/"):
        return "project"
    return "page"


def _page_title(kind: str, slug: str, fm: dict) -> str:
    if kind == "role":
        return fm.get("company") or fm.get("role") or slug
    if kind == "project":
        return fm.get("name") or slug
    if kind == "profile":
        return fm.get("name") or "Profile"
    if kind == "skills":
        return "Technical Skills"
    return slug


def _wiki_files():
    return sorted(glob.glob(os.path.join(paths.career_dir(), "**", "*.md"), recursive=True))


def _slug_of(path: str) -> str:
    rel = os.path.relpath(path, paths.career_dir())
    return rel[:-3] if rel.endswith(".md") else rel


def list_pages() -> list[dict]:
    """Index of every wiki page: {kind, slug, title}. The agent's LIST tool."""
    _ensure_dirs()
    pages = []
    for p in _wiki_files():
        slug = _slug_of(p)
        fm, _ = parse_md(open(p).read())
        kind = _page_kind(slug)
        pages.append({"kind": kind, "slug": slug, "title": _page_title(kind, slug, fm)})
    return pages


def _snippet(body: str, terms: list[str], width: int = 180) -> str:
    flat = " ".join((body or "").split())
    low = flat.lower()
    pos = min((low.find(t) for t in terms if low.find(t) >= 0), default=-1)
    if pos < 0:
        return flat[:width]
    start = max(0, pos - 40)
    return ("…" if start else "") + flat[start:start + width] + ("…" if start + width < len(flat) else "")


def search_wiki(query: str, *, limit: int = 8) -> list[dict]:
    """Keyword-rank wiki pages by term overlap with the query. The FIND tool.
    Returns [{kind, slug, title, snippet, score}] sorted by score desc."""
    _ensure_dirs()
    terms = [t for t in re.split(r"[^a-z0-9]+", (query or "").lower()) if len(t) > 1]
    if not terms:
        return []
    results = []
    for p in _wiki_files():
        text = open(p).read()
        low = text.lower()
        score = sum(low.count(t) for t in terms)
        if score <= 0:
            continue
        slug = _slug_of(p)
        fm, body = parse_md(text)
        kind = _page_kind(slug)
        results.append({"kind": kind, "slug": slug, "title": _page_title(kind, slug, fm),
                        "snippet": _snippet(body or text, terms), "score": score})
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def read_page(slug: str) -> str | None:
    """Full text of one wiki page by slug (e.g. 'roles/saal'). The READ tool.
    Returns None for unknown slugs or any path-escape attempt."""
    slug = (slug or "").strip().strip("/")
    if slug.endswith(".md"):
        slug = slug[:-3]
    if not slug:
        return None
    career = os.path.normpath(paths.career_dir())
    path = os.path.normpath(os.path.join(career, slug + ".md"))
    if os.path.commonpath([career, path]) != career:
        return None  # path traversal attempt
    if not os.path.exists(path):
        return None
    return open(path).read()
