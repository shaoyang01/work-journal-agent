param(
  [string]$EventType = ""
)

$inputJson = [Console]::In.ReadToEnd()
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)

if (Get-Command wj -ErrorAction SilentlyContinue) {
  if ($EventType -ne "") {
    $inputJson | wj claude-hook --event-type $EventType
  } else {
    $inputJson | wj claude-hook
  }
  exit $LASTEXITCODE
}

if (Get-Command py -ErrorAction SilentlyContinue) {
  $env:PYTHONPATH = Join-Path $ProjectRoot "src"
  if ($EventType -ne "") {
    $inputJson | py -3 -m work_journal_agent claude-hook --event-type $EventType
  } else {
    $inputJson | py -3 -m work_journal_agent claude-hook
  }
  exit $LASTEXITCODE
}

if (Get-Command python -ErrorAction SilentlyContinue) {
  $env:PYTHONPATH = Join-Path $ProjectRoot "src"
  if ($EventType -ne "") {
    $inputJson | python -m work_journal_agent claude-hook --event-type $EventType
  } else {
    $inputJson | python -m work_journal_agent claude-hook
  }
  exit $LASTEXITCODE
}

Write-Error "Python 3.11+ or installed wj command is required."
exit 1
