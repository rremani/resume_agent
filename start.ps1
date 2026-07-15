# resume-agent — one-run installer for native Windows (PowerShell).
#
#   Right-click > Run with PowerShell, or:
#     powershell -ExecutionPolicy Bypass -File start.ps1
#
# After it finishes, open a new terminal and type:  resume
#
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Say($m) { Write-Host "> $m" -ForegroundColor Cyan }
function Ok($m)  { Write-Host "OK $m" -ForegroundColor Green }

# ---- 1. Python 3.10+ ----------------------------------------------------
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) {
  Write-Error "Python not found. Install Python 3.10+ from https://python.org and re-run."
  exit 1
}
& $py.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if ($LASTEXITCODE -ne 0) { Write-Error "Python 3.10+ is required."; exit 1 }
Ok "Python found"

# ---- 2. uv --------------------------------------------------------------
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Say "Installing uv..."
  powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Error "uv install failed - see https://docs.astral.sh/uv/"
  exit 1
}
Ok "uv installed"

# ---- 3. install the `resume` command globally ---------------------------
Say "Installing resume-agent (this pulls all dependencies)..."
uv tool install . --reinstall

# ---- 4. put it on PATH for future terminals -----------------------------
uv tool update-shell 2>$null

Write-Host ""
Ok "Installed."
Write-Host ""
Write-Host "  Type  resume  to start."
Write-Host "  (If 'resume' isn't found, open a NEW terminal first - PATH was just updated.)"
