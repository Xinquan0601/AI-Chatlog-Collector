# AgentTrail 启动脚本（Windows PowerShell）
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = $null
if ($env:AGENTTRAIL_PYTHON) { $Python = $env:AGENTTRAIL_PYTHON }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $Python = (Get-Command python).Source }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $Python = (Get-Command py).Source }

if (-not $Python) {
  $Bundled = Join-Path $env:USERPROFILE ".dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
  if (Test-Path -LiteralPath $Bundled) { $Python = $Bundled }
}

if (-not $Python) {
  Write-Host "未找到 Python。请安装 Python 3.11+ 或设置 AGENTTRAIL_PYTHON 环境变量。" -ForegroundColor Red
  exit 1
}

Write-Host "使用 Python: $Python" -ForegroundColor DarkGray
& $Python "app.py" @args
