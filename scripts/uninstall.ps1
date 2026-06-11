$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

if (Get-Command wj -ErrorAction SilentlyContinue) {
  & wj uninstall @args
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
  $env:PYTHONPATH = Join-Path $ProjectRoot "src"
  & py -3 -m work_journal_agent uninstall @args
} else {
  $env:PYTHONPATH = Join-Path $ProjectRoot "src"
  & python -m work_journal_agent uninstall @args
}

if (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3 -m pip uninstall -y work-journal-agent
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  & python -m pip uninstall -y work-journal-agent
}

