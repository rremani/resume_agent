"""
Document -> Markdown extraction for the raw layer.

Primary: Microsoft MarkItDown — converts PDF/DOCX/PPTX/XLSX/HTML and more into
clean, structure-preserving Markdown that LLMs read natively. Because our raw
layer IS markdown, this output drops straight in with no reformatting, and the
compiler sees real section/list/table structure instead of a flat text blob.

Fallback: pypdf text extraction, used only if markitdown is unavailable or
returns nothing (e.g. a damaged file). Scanned/image-only PDFs need OCR
(markitdown-ocr plugin) and are out of scope for v1 — we surface a clear note.

Note on fidelity: markitdown is ~82% F1 on complex multi-column / table-heavy
PDF layouts. That's fine here because bootstrap is human-reviewed and the
raw-grounding step catches extraction errors before they reach a resume.
"""

from __future__ import annotations
import os


def extract_to_markdown(path: str) -> tuple[str, str]:
    """Return (markdown_text, method). method is 'markitdown' or 'pypdf'.
    Raises FileNotFoundError if the path is missing."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    # --- primary: markitdown (any supported format) ---
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(path)
        text = (result.text_content or "").strip()
        if text:
            return text, "markitdown"
    except Exception:
        pass  # fall through to pypdf

    # --- fallback: pypdf (PDF text layer only) ---
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        text = "\n".join((pg.extract_text() or "") for pg in reader.pages).strip()
        if text:
            return text, "pypdf"
    except Exception:
        pass

    return "", "none"
