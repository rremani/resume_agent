"""Tests for the deterministic resume renderer (skill/render.py).

Focus: the renderer must produce BOTH a .docx and a .pdf from the same
resume-YAML model, in pure Python, with NO subprocess / LibreOffice call.
"""
import os
import sys

import pytest
from pypdf import PdfReader

# Make `skill/render.py` importable as `render`.
SKILL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skill")
sys.path.insert(0, SKILL_DIR)
import render  # noqa: E402


SAMPLE = {
    "name": "Ada Lovelace",
    "title": "Data Scientist",
    "contact": {
        "email": "ada@example.com",
        "phone": "+1 555 0100",
        "location": "London, UK",
        "linkedin": "linkedin.com/in/ada",
        "github": "github.com/ada",
    },
    "summary": "Engineer with 9 years building analytical engines and ML systems.",
    "skills": [
        {"category": "ML", "tags": ["ml"], "items": ["PyTorch", "XGBoost"]},
        {"category": "Web", "tags": ["web"], "items": ["FastAPI", "Docker"]},
    ],
    "experience": [
        {
            "role": "Senior Engineer",
            "company": "Analytical Engines Ltd",
            "start": "03/2023",
            "end": "Present",
            "location": "London",
            "context": "Built the difference engine pipeline.",
            "bullets": [
                {"text": "Improved throughput by 90% on the core pipeline.",
                 "tags": ["ml"]},
                {"text": "Shipped a FastAPI service handling 200 req/s.",
                 "tags": ["web"]},
            ],
        },
        {
            "role": "Mathematician",
            "company": "Royal Society",
            "start": "01/2020",
            "end": "02/2023",
            "location": "London",
            "bullets": [
                {"text": "Authored the first algorithm with 75% adoption.",
                 "tags": ["ml"]},
            ],
        },
    ],
    "education": [
        {"degree": "BSc Mathematics", "institution": "University of London",
         "start": "09/2012", "end": "06/2014", "location": "London"},
    ],
}


@pytest.fixture
def out_stem(tmp_path):
    return str(tmp_path / "resume_test")


def test_build_returns_both_paths_that_exist(out_stem):
    docx_path, pdf_path = render.build(SAMPLE, None, out_stem)
    assert docx_path.endswith(".docx")
    assert pdf_path.endswith(".pdf")
    assert os.path.exists(docx_path), "DOCX not written"
    assert os.path.exists(pdf_path), "PDF not written"


def test_no_subprocess_or_soffice_anywhere_in_repo():
    """The whole point of Task 1: zero subprocess / soffice in the renderer."""
    src = open(render.__file__).read()
    assert "soffice" not in src
    assert "subprocess" not in src
    assert "libreoffice" not in src.lower()


def test_pdf_is_valid_and_at_most_two_pages(out_stem):
    _, pdf_path = render.build(SAMPLE, None, out_stem)
    reader = PdfReader(pdf_path)
    assert 1 <= len(reader.pages) <= 2


def test_pdf_contains_key_facts(out_stem):
    _, pdf_path = render.build(SAMPLE, None, out_stem)
    text = "".join(p.extract_text() for p in PdfReader(pdf_path).pages)
    assert "Ada Lovelace" in text
    assert "Analytical Engines Ltd" in text
    assert "90%" in text          # a metric must survive — grounding depends on it
    assert "University of London" in text


def test_tag_filter_drops_unmatched_bullets(out_stem):
    _, pdf_path = render.build(SAMPLE, {"web"}, out_stem)
    text = "".join(p.extract_text() for p in PdfReader(pdf_path).pages)
    assert "FastAPI service" in text          # web-tagged bullet kept
    assert "throughput" not in text           # ml-only bullet dropped


def test_empty_skill_groups_are_dropped(out_stem):
    """A skill group with no items must not render as a dangling header."""
    data = dict(SAMPLE)
    data["skills"] = [
        {"category": "Phantom", "tags": [], "items": []},      # empty → skip
        {"category": "Phantom", "tags": [], "items": []},      # duplicate empty
        {"category": "RealSkills", "tags": [], "items": ["PyTorch"]},
    ]
    _, pdf_path = render.build(data, None, out_stem)
    text = "".join(p.extract_text() for p in PdfReader(pdf_path).pages)
    assert "RealSkills" in text
    assert "Phantom" not in text


def test_experience_sorted_reverse_chronological(out_stem):
    """Roles must render newest-first regardless of the input order."""
    data = dict(SAMPLE)
    data["experience"] = [
        {"role": "Older", "company": "OldCo", "start": "02/2017", "end": "05/2019",
         "bullets": [{"text": "old work", "tags": []}]},
        {"role": "Newest", "company": "NewCo", "start": "03/2023", "end": "Present",
         "bullets": [{"text": "new work", "tags": []}]},
        {"role": "Middle", "company": "MidCo", "start": "06/2020", "end": "01/2021",
         "bullets": [{"text": "mid work", "tags": []}]},
    ]
    _, pdf_path = render.build(data, None, out_stem)
    text = "".join(p.extract_text() for p in PdfReader(pdf_path).pages)
    assert text.index("NewCo") < text.index("MidCo") < text.index("OldCo")
