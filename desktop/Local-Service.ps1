function Get-LocalRecorderLaunch([string] $ExpectedAddress) {
    $launchPath = Join-Path $env:LOCALAPPDATA 'DaiguiRecorder\local-launch.json'
    $launch = Get-Content -LiteralPath $launchPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not ([string] $launch.url).StartsWith($ExpectedAddress + '#access=', [StringComparison]::Ordinal)) { throw '本机工作台地址不符，请重新启动。' }
    return $launch.url
}
function Start-LocalRecorder {
    param([int] $Port = 18487)
    $recorderRoot = Split-Path -Parent $PSScriptRoot
    $recorderAddress = 'http://127.0.0.1:' + $Port + '/recorder/'
    try {
        $health = Invoke-RestMethod -Uri ($recorderAddress + 'api/health') -TimeoutSec 2
        if ($health.application -eq 'daigui-recorder' -and $health.mode -eq 'local') { return (Get-LocalRecorderLaunch $recorderAddress) }
        throw '该端口不是本机录制工作台。'
    } catch {
        if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
            throw '本机录制端口已被使用。请先关闭原服务，或调整 RECORDER_LOCAL_PORT 后重试。'
        }
    }
    $recorderPythonPath = Join-Path $recorderRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $recorderPythonPath -PathType Leaf)) {
        throw '请先在 PowerShell 运行 desktop\Install-Local.ps1 安装本机录制依赖。'
    }
    $recorderStateDirectory = Join-Path $env:LOCALAPPDATA 'DaiguiRecorder'
    [void] [IO.Directory]::CreateDirectory($recorderStateDirectory)
    $recorderRunnerPath = Join-Path $recorderRoot 'server\local_runner.py'
    $recorderProcess = Start-Process -FilePath $recorderPythonPath -ArgumentList ('"' + $recorderRunnerPath + '"') `
        -WorkingDirectory $recorderRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $recorderStateDirectory 'local-service.stdout.log') `
        -RedirectStandardError (Join-Path $recorderStateDirectory 'local-service.stderr.log')
    $recorderDeadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        try {
            $health = Invoke-RestMethod -Uri ($recorderAddress + 'api/health') -TimeoutSec 2
            if ($health.application -eq 'daigui-recorder' -and $health.mode -eq 'local' -and $health.ok) { return (Get-LocalRecorderLaunch $recorderAddress) }
        } catch { }
        if ($recorderProcess.HasExited) { throw '本机录制服务启动失败，请查看本机服务日志后重试。' }
        Start-Sleep -Milliseconds 300
    } while ([DateTime]::UtcNow -lt $recorderDeadline)
    throw '本机录制服务启动超时，请检查依赖后重试。'
}
