<#
  Xylem bootstrap for Windows.
  Locates a Python 3.8+ interpreter and hands off to installer.py.
  All real logic lives in installer.py; this only finds Python and forwards args.
#>
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Installer = Join-Path $ScriptDir "installer.py"

function Test-PyVersion([string]$exe) {
  try {
    & $exe -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>$null
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  }
}

$Py = $null
# Prefer the Windows launcher, then python/python3 on PATH.
foreach ($candidate in @("py", "python", "python3")) {
  $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
  if ($null -ne $cmd) {
    if ($candidate -eq "py") {
      # Use the launcher's newest 3.x.
      try { & $candidate -3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>$null } catch {}
      if ($LASTEXITCODE -eq 0) { $Py = @("py", "-3"); break }
    } elseif (Test-PyVersion $cmd.Source) {
      $Py = @($cmd.Source)
      break
    }
  }
}

if ($null -eq $Py) {
  Write-Error "xylem: could not find Python 3.8+ (looked for py, python, python3). Install Python 3.8+ and re-run."
  exit 1
}

$exe = $Py[0]
$prefix = @()
if ($Py.Count -gt 1) { $prefix = $Py[1..($Py.Count - 1)] }

& $exe @prefix $Installer @args
exit $LASTEXITCODE
