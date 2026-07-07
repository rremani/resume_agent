"""Deterministic render skill (resume YAML → DOCX + PDF).

Self-contained by design: imports nothing from `core`. Exposed as a package so
the renderer can be called in-process from an installed or frozen build.
"""
