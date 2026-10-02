param(
    [string] $PythonPath = 'python',
    [switch] $SkipShortcut
)

$ErrorActionPreference = 'Stop'
$recorderRoot = Split-Path -Parent $PSScriptRoot
$recorderPython = Get-Command $PythonPath -ErrorAction SilentlyContinue
if (-not $recorderPython) { throw '请先安装 Python 3.11 或 3.12，并勾选 Add Python to PATH。' }
$recorderVersionScript = 'import sys; print(str(sys.version_info.major) + chr(46) + str(sys.version_info.minor))'
$recorderVersion = & $recorderPython.Source -c $recorderVersionScript
if ($LASTEXITCODE -ne 0 -or $recorderVersion.Trim() -notin @('3.11', '3.12')) { throw '本版本需要 Python 3.11 或 3.12，请使用 -PythonPath 指定对应解释器。' }
$recorderVenvPython = Join-Path $recorderRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $recorderVenvPython -PathType Leaf)) {
    & $recorderPython.Source -m venv (Join-Path $recorderRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '无法创建 Python 环境。' }
}
$recorderEnvironmentVersion = & $recorderVenvPython -c $recorderVersionScript
if ($LASTEXITCODE -ne 0 -or $recorderEnvironmentVersion.Trim() -notin @('3.11', '3.12')) { throw '现有虚拟环境使用了不支持的 Python。请在新目录安装，并保留原配置与数据。' }
& $recorderVenvPython -m pip install --require-hashes -r (Join-Path $recorderRoot 'server\requirements.lock')
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请检查网络后重试。' }
if (-not $SkipShortcut) {
    $recorderDesktop = [Environment]::GetFolderPath('Desktop')
    $recorderShortcutPath = Join-Path $recorderDesktop '今日录制 · Record to YouTube.lnk'
    $recorderShell = New-Object -ComObject WScript.Shell
    $recorderShortcut = $recorderShell.CreateShortcut($recorderShortcutPath)
    if (-not (Test-Path -LiteralPath $recorderShortcutPath)) {
        $recorderShortcut.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
        $recorderShortcut.Arguments = '"' + (Join-Path $PSScriptRoot 'Start-Recorder.vbs') + '"'
        $recorderShortcut.WorkingDirectory = $recorderRoot
    }
    if ($recorderShortcut.WorkingDirectory -eq $recorderRoot) {
        $recorderShortcut.Description = '今日录制 · Record to YouTube'
        $recorderShortcut.IconLocation = (Join-Path $PSScriptRoot 'project-icon.ico') + ',0'
        $recorderShortcut.Save()
    }
}
$recorderRelease = (Get-Content -LiteralPath (Join-Path $recorderRoot 'VERSION') -Raw).Trim()
Write-Output ('Record to YouTube ' + $recorderRelease + ' 安装完成。双击 desktop\Start-Recorder.vbs 可录制；连接 YouTube 前配置 desktop\local.env。')
