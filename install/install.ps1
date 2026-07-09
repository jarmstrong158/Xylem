<#
  Thin bootstrap for Windows: find a Python 3 interpreter and hand off to the
  installer. All real logic lives in xylem_install.py — keep this dumb.

    .\install.ps1                  # dry-run: show what would change
    .\install.ps1 install -apply    # write the changes  (see note below)
    .\install.ps1 uninstall         # dry-run removal
    .\install.ps1 list-agents       # what's detected here

  With no arguments it defaults to a dry-run `install` so a curious run is safe.
  Arguments after the script name are passed straight through to Python, e.g.
    .\install.ps1 install --apply
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

$py = Find-Python3
if ($null -eq $py) {
    Write-Error "Python 3 is required but was not found on PATH. Install it from https://www.python.org/downloads/ and re-run."
    exit 1
}

$passthru = if ($args.Count -eq 0) { @("install") } else { $args }
& $py[0] @($py[1..($py.Count-1)]) $installer @passthru
exit $LASTEXITCODE
