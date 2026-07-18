# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for a single-file `resume` binary (Tier B distribution).

Produces a self-contained executable that runs `resume bootstrap/fast/think`
on a machine with NO Python and NO LibreOffice. The PDF path is pure-Python
(ReportLab), so nothing external is shelled out.

The heavy lifting here is force-collecting packages that load data files or
sub-modules dynamically (PyInstaller's static analysis misses these):
  - markitdown   : extractor plugins + magika ONNX model data
  - reportlab    : font metrics / .pfb data files
  - docx         : the default .docx template
Build with:  pyinstaller resume.spec --noconfirm
Output:      dist/resume
"""
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
# Packages whose data files / dynamic imports must be bundled explicitly.
for pkg in ("markitdown", "reportlab", "docx", "litellm", "langgraph",
            "magika", "pypdf", "prompt_toolkit"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        # Optional packages (e.g. magika) may not resolve on every platform.
        pass

a = Analysis(
    ["cli.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="resume",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
