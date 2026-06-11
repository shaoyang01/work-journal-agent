$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

if (Get-Command wj -ErrorAction SilentlyContinue) {
  & wj setup @args
  exit $LASTEXITCODE
}

if (Get-Command py -ErrorAction SilentlyContinue) {
  $env:PYTHONPATH = Join-Path $ProjectRoot "src"
  & py -3 -m work_journal_agent setup @args
  exit $LASTEXITCODE
}

if (Get-Command python -ErrorAction SilentlyContinue) {
  $env:PYTHONPATH = Join-Path $ProjectRoot "src"
  & python -m work_journal_agent setup @args
  exit $LASTEXITCODE
}

Write-Error "Python 3.11+ is required but was not found."
exit 1

