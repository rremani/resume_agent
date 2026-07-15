#!/usr/bin/env bash
#
# resume-agent — one-run installer.
#
#   macOS / Linux:      ./start.sh
#   Windows:            use Git Bash or WSL and run ./start.sh
#                       (or run start.ps1 in PowerShell — see that file)
#
# After it finishes, open a new terminal and type:  resume
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

say()  { printf '\033[1;36m›\033[0m %s\n' "$1"; }
err()  { printf '\033[1;31m✗ %s\033[0m\n' "$1" >&2; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$1"; }

# ---- 1. Python 3.10+ ----------------------------------------------------
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  err "Python not found. Install Python 3.10+ from https://python.org and re-run."
  exit 1
fi
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'; then
  err "Python $("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])') found, but 3.10+ is required."
  exit 1
fi
ok "Python $("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"

# ---- 2. uv (installs the global `resume` command with all deps) --------
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # make uv usable in THIS shell right away
  [ -f "$HOME/.local/bin/env" ] && . "$HOME/.local/bin/env" || true
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
  err "uv install failed — see https://docs.astral.sh/uv/ and try again."
  exit 1
fi
ok "uv $(uv --version | awk '{print $2}')"

# ---- 3. install the `resume` command globally ---------------------------
say "Installing resume-agent (this pulls all dependencies)…"
uv tool install . --reinstall

# ---- 4. put it on PATH for future terminals -----------------------------
uv tool update-shell >/dev/null 2>&1 || true

echo
ok "Installed."
echo
printf "  Type  \033[1mresume\033[0m  to start.\n"
printf "  (If 'resume' isn't found, open a NEW terminal first — PATH was just updated.)\n"
