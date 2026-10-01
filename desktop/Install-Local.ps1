$ErrorActionPreference = 'Stop'
$recorderRoot = Split-Path -Parent $PSScriptRoot
$recorderPython = Get-Command python -ErrorAction SilentlyContinue
if (-not $recorderPython) { throw '请先安装 Python 3.11 或 3.12，并勾选 Add Python to PATH。' }
& $recorderPython.Source -m venv (Join-Path $recorderRoot '.venv')
if ($LASTEXITCODE -ne 0) { throw '无法创建 Python 环境。' }
$recorderVenvPython = Join-Path $recorderRoot '.venv\Scripts\python.exe'
& $recorderVenvPython -m pip install -r (Join-Path $recorderRoot 'server\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请检查网络后重试。' }
Write-Output '安装完成。双击 desktop\Start-Recorder.vbs 可录制；连接 YouTube 前配置 desktop\local.env。'
