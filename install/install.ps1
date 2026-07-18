<#
  Thin bootstrap for Windows: find a Python 3 interpreter and hand off to the
  installer. All real logic lives in xylem_install.py — keep this dumb.

  THIS SCRIPT DRY-RUNS BY DEFAULT. Nothing is written unless you pass --apply.
  (Note: the repo-root .\install.sh is a DIFFERENT script that applies
  immediately. Same filename, opposite default — check which one you are running.)

    .\install.ps1                   # DRY-RUN install: show what would change
    .\install.ps1 install --apply   # actually write the changes
    .\install.ps1 uninstall         # DRY-RUN removal
    .\install.ps1 uninstall --apply # actually remove
    .\install.ps1 list-agents       # what's detected here

  With no arguments it defaults to a dry-run `install` so a curious run is safe.
  Arguments after the script name are passed straight through to Python.
#>

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$installer = Join-Path $scriptDir "xylem_install.py"

function Find-Python3 {
    foreach ($cand in @("python3", "python", "py")) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if ($null -ne $cmd) {
            try {
                $args = if ($cand -eq "py") { @("-3", "-c", "import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)") }
                        else { @("-c", "import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)") }
                & $cmd.Source @args 2>$null
                if ($LASTEXITCODE -eq 0) {
                    if ($cand -eq "py") { return @($cmd.Source, "-3") }
                    return @($cmd.Source)
                }
            } catch { }
        }
    }
    return $null
}

# @(...) is REQUIRED: PowerShell unwraps a single-element array returned from a
# function into a bare string, and indexing a string yields a CHARACTER — so
# $py[0] became "C" and the launch failed with "The term 'C' is not recognized".
$py = @(Find-Python3)
if ($py.Count -eq 0) {
    Write-Error "Python 3 is required but was not found on PATH. Install it from https://www.python.org/downloads/ and re-run."
    exit 1
}

# @(...) again: without it a single argument unwraps to a string and @passthru
# splats it one CHARACTER at a time ("invalid choice: 'l'").
$passthru = @(if ($args.Count -eq 0) { "install" } else { $args })

# NOTE: do NOT use $py[1..($py.Count-1)] — for a 1-element array that is the
# DESCENDING range 1..0, which yields the element itself, so the interpreter
# path gets passed to itself as a script argument ("can't open file ...").
$exe = $py[0]
$prefix = @()
if ($py.Count -gt 1) { $prefix = $py[1..($py.Count - 1)] }

# Loud, unmistakable mode banner — this script DRY-RUNS by default.
$mode = if ($passthru -contains "--apply") { "APPLY (files WILL be written)" } else { "DRY-RUN (no files written; add --apply to write)" }
Write-Host "=== Xylem installer: $mode ===" -ForegroundColor Cyan

& $exe @prefix $installer @passthru
exit $LASTEXITCODE
