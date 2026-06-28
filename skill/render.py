#!/usr/bin/env python3
"""
Generate a FAANG-style monochrome resume (.docx + .pdf) from a YAML file.

Usage:
  python generate_faang.py
  python generate_faang.py --tags genai nlp mlops --out resume_genai
  python generate_faang.py --tags finance forecasting --out resume_banking
"""

import argparse
import subprocess
import yaml
from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

BLACK = RGBColor(0x00, 0x00, 0x00)
DARK = RGBColor(0x22, 0x22, 0x22)
FONT = "Georgia"          # classic, FAANG-typical serif; swap to "Calibri" for sans
CONTENT_WIDTH_IN = 7.1    # 8.5 - 2*0.7 margins


def keep(item_tags, wanted):
    return True if not wanted else any(t in wanted for t in (item_tags or []))


def add_hrule(paragraph, color="000000", sz="4"):
    pPr = paragraph._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), sz)
    bottom.set(qn("w:space"), "2")
    bottom.set(qn("w:color"), color)
    pbdr.append(bottom)
    pPr.append(pbdr)


def section_heading(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run(text.upper())
    r.bold = True
    r.font.size = Pt(11)
    r.font.color.rgb = BLACK
    r.font.name = FONT
    # letter spacing
    rPr = r._element.get_or_add_rPr()
    spc = OxmlElement("w:spacing")
    spc.set(qn("w:val"), "30")
    rPr.append(spc)
    add_hrule(p)
    return p


def tabbed_line(doc, left_runs, right_text, right_italic=True, size=10):
    """One paragraph: left content, right-aligned date via right tab stop."""
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.space_before = Pt(0)
    tabs = p.paragraph_format.tab_stops
    tabs.add_tab_stop(Inches(CONTENT_WIDTH_IN), WD_TAB_ALIGNMENT.RIGHT)
    for txt, bold, italic in left_runs:
        r = p.add_run(txt)
        r.bold = bold
        r.italic = italic
        r.font.size = Pt(size)
        r.font.name = FONT
        r.font.color.rgb = DARK
    r = p.add_run("\t" + right_text)
    r.italic = right_italic
    r.font.size = Pt(size - 0.5)
    r.font.name = FONT
    r.font.color.rgb = DARK
    return p


def build(data, wanted, out_stem):
    doc = Document()
    for s in doc.sections:
        s.top_margin = Inches(0.6)
        s.bottom_margin = Inches(0.6)
        s.left_margin = Inches(0.7)
        s.right_margin = Inches(0.7)
    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(10)
    normal.font.color.rgb = DARK

    # Header
    name_p = doc.add_paragraph()
    name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name_p.paragraph_format.space_after = Pt(0)
    nr = name_p.add_run(data["name"])
    nr.bold = True
    nr.font.size = Pt(20)
    nr.font.name = FONT
    nr.font.color.rgb = BLACK
    rPr = nr._element.get_or_add_rPr()
    spc = OxmlElement("w:spacing"); spc.set(qn("w:val"), "40"); rPr.append(spc)

    c = data["contact"]
    bits = [c.get("email"), c.get("phone"), c.get("location"),
            c.get("linkedin"), c.get("github"), c.get("medium")]
    bits = [b for b in bits if b]
    cp = doc.add_paragraph()
    cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cp.paragraph_format.space_before = Pt(2)
    cp.paragraph_format.space_after = Pt(2)
    cr = cp.add_run("  |  ".join(bits))
    cr.font.size = Pt(8.5)
    cr.font.name = FONT
    cr.font.color.rgb = DARK

    # Summary
    if data.get("summary"):
        section_heading(doc, "Summary")
        sp = doc.add_paragraph(data["summary"].strip())
        sp.paragraph_format.space_after = Pt(3)
        for r in sp.runs:
            r.font.size = Pt(9.5); r.font.name = FONT; r.font.color.rgb = DARK

    # Experience
    jobs = []
    for job in data.get("experience", []):
        kept = [b for b in job.get("bullets", []) if keep(b.get("tags"), wanted)]
        if kept:
            jobs.append((job, kept))
    if jobs:
        section_heading(doc, "Experience")
        for job, kept in jobs:
            dates = f'{job.get("start","")} \u2013 {job.get("end","")}'
            tabbed_line(doc,
                        [(job["role"] + "  —  ", True, False),
                         (job["company"], False, False)],
                        dates, size=10.5)
            if job.get("location"):
                loc_p = doc.add_paragraph()
                loc_p.paragraph_format.space_after = Pt(1)
                lr = loc_p.add_run(job["location"])
                lr.italic = True
                lr.font.size = Pt(8.5)
                lr.font.name = FONT
                lr.font.color.rgb = DARK
            for b in kept:
                bp = doc.add_paragraph(style="List Bullet")
                bp.paragraph_format.space_after = Pt(2)
                bp.paragraph_format.space_before = Pt(0)
                br = bp.add_run(" ".join(b["text"].split()))
                br.font.size = Pt(9.5)
                br.font.name = FONT
                br.font.color.rgb = DARK

    # Skills
    groups = [g for g in data.get("skills", []) if keep(g.get("tags"), wanted)]
    if groups:
        section_heading(doc, "Technical Skills")
        for g in groups:
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(2)
            r1 = p.add_run(g["category"] + ":  ")
            r1.bold = True; r1.font.size = Pt(9.5); r1.font.name = FONT; r1.font.color.rgb = BLACK
            r2 = p.add_run(" · ".join(g["items"]))
            r2.font.size = Pt(9.5); r2.font.name = FONT; r2.font.color.rgb = DARK

    # Education
    if data.get("education"):
        section_heading(doc, "Education")
        for e in data["education"]:
            dates = f'{e.get("start","")} \u2013 {e.get("end","")}'
            tabbed_line(doc,
                        [(e["degree"], True, False)],
                        dates, size=9.5)
            sub = e["institution"] + (f'  |  {e["location"]}' if e.get("location") else "")
            sp = doc.add_paragraph()
            sp.paragraph_format.space_after = Pt(3)
            sr = sp.add_run(sub)
            sr.italic = True; sr.font.size = Pt(9); sr.font.name = FONT; sr.font.color.rgb = DARK

    import os as _os
    out_dir = _os.path.dirname(out_stem) or "/home/claude"
    docx_path = f"{out_stem}.docx"
    doc.save(docx_path)
    subprocess.run(["soffice", "--headless", "--convert-to", "pdf",
                    "--outdir", out_dir, docx_path],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return docx_path, f"{out_stem}.pdf"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", default="store/career_history.yaml")
    ap.add_argument("--tags", nargs="*", default=None)
    ap.add_argument("--out", default="resume_faang")
    args = ap.parse_args()
    with open(args.yaml) as f:
        data = yaml.safe_load(f)
    wanted = set(args.tags) if args.tags else None
    d, p = build(data, wanted, args.out)
    print(d); print(p)


if __name__ == "__main__":
    main()
