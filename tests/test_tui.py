"""Tests for the inline think TUI (tui.py).

Interactive prompt_toolkit behavior can't be exercised without a real terminal,
so these cover the pure block builders and the input seam (read_line), which is
stubbed to drive the REPL without a TTY.
"""
import os
import sys

CORE_PARENT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, CORE_PARENT)
import tui  # noqa: E402


def _render(renderable) -> str:
    from rich.console import Console
    c = Console(width=100)
    with c.capture() as cap:
        c.print(renderable)
    return cap.get()


# ---- block builders ----------------------------------------------------

def test_section_block_renders_title_and_body():
    txt = _render(tui.section_block("Eng — Acme", "  1. Built X\n  2. Shipped Y"))
    assert "Eng — Acme" in txt and "Built X" in txt and "Shipped Y" in txt


def test_result_block_ok_and_paths():
    txt = _render(tui.result_block(
        {"ok": True, "problems": [], "yaml": "/o/t.yaml", "pdf": "/o/t.pdf", "docx": "/o/t.docx"}))
    assert "Grounding passed" in txt and "t.pdf" in txt


def test_result_block_flags_when_not_ok():
    txt = _render(tui.result_block({"ok": False, "problems": ["Number not in store: '9'"]}))
    assert "Grounding flags" in txt and "Number not in store" in txt


def test_result_block_warns_about_estimates():
    txt = _render(tui.result_block({"ok": True, "estimates": True, "yaml": "/o/t.yaml"}))
    assert "ESTIMATES" in txt and "verify" in txt


# ---- input seam --------------------------------------------------------

def _repl():
    return tui.ReplApp({"provider": "anthropic", "modes": {"think": {"model": "x"}}})


def test_ask_strips_via_read_line(monkeypatch):
    r = _repl()
    monkeypatch.setattr(r, "read_line", lambda *a, **k: "  done  ")
    assert r.ask() == "done"


def test_ask_quits_on_interrupt(monkeypatch):
    r = _repl()
    def boom(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(r, "read_line", boom)
    assert r.ask() == "quit"


# ---- REPL dispatch (slash routing) -------------------------------------

def test_dispatch_routes_and_quits(monkeypatch):
    r = _repl()
    calls = []
    monkeypatch.setattr(r, "think", lambda b: calls.append(("think", b)))
    monkeypatch.setattr(r, "fast", lambda b: calls.append(("fast", b)))
    monkeypatch.setattr(r, "add", lambda b: calls.append(("add", b)))
    monkeypatch.setattr(r, "_help", lambda: calls.append(("help",)))

    assert r._dispatch("quit") is False
    assert r._dispatch("/quit") is False
    assert r._dispatch("data scientist role") is True
    assert calls[-1] == ("think", "data scientist role")          # plain → deep think
    assert r._dispatch("/fast senior ML") is True
    assert calls[-1] == ("fast", "senior ML")
    assert r._dispatch("/add did a project") is True
    assert calls[-1] == ("add", "did a project")
    r._dispatch("/help")
    assert calls[-1] == ("help",)


def test_provided_facts_shown_in_result_block():
    txt = _render(tui.result_block(
        {"ok": True, "problems": [], "provided": ["Kubernetes at Acme"],
         "yaml": "/o/t.yaml"}))
    assert "you provided these this session" in txt and "Kubernetes at Acme" in txt
