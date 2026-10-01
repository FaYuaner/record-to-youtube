$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\desktop\Recorder-Settings.ps1')
$taskTestRoot = Join-Path $PSScriptRoot ('..\artifacts\desktop-settings-' + [Guid]::NewGuid().ToString('N'))
[void] [IO.Directory]::CreateDirectory($taskTestRoot)
$taskChromePath = Join-Path $taskTestRoot 'fixture-chrome.exe'
[IO.File]::WriteAllText($taskChromePath, 'fixture only - never executed')
$taskChromePath = [IO.Path]::GetFullPath($taskChromePath)
$taskConfigPath = Join-Path $taskTestRoot 'settings.json'

function Assert-ConfigurationRejected([string] $Path) {
    $rejected = $false
    try { Get-RecorderSettings -ConfigPath $Path | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw 'Invalid or missing server configuration was accepted.' }
}

# Local mode needs no server URL or private configuration.
@{ mode = 'remote'; serverUrl = ''; chromePath = $taskChromePath; profileDirectory = '' } |
    ConvertTo-Json | Set-Content -LiteralPath $taskConfigPath -Encoding UTF8
Assert-ConfigurationRejected $taskConfigPath

@{ mode = 'remote'; serverUrl = 'http://recorder.example.test/recorder'; chromePath = $taskChromePath; profileDirectory = '' } |
    ConvertTo-Json | Set-Content -LiteralPath $taskConfigPath -Encoding UTF8
Assert-ConfigurationRejected $taskConfigPath

@{ serverUrl = 'https://my-recorder.example.test/recorder'; chromePath = $taskChromePath; profileDirectory = '' } |
    ConvertTo-Json | Set-Content -LiteralPath $taskConfigPath -Encoding UTF8
$taskResolved = Get-RecorderSettings -ConfigPath $taskConfigPath
if ($taskResolved.ServerUrl -ne 'https://my-recorder.example.test/recorder/') { throw 'Wrong server URL.' }
if ($taskResolved.ChromePath -ne $taskChromePath) { throw 'Wrong Chrome path.' }
if ($taskResolved.ProfileDirectory -ne (Join-Path $env:LOCALAPPDATA 'DaiguiRecorder\ChromeProfile')) { throw 'Wrong per-user browser directory.' }
@{ mode = 'local'; serverUrl = ''; chromePath = $taskChromePath; profileDirectory = '' } |
    ConvertTo-Json | Set-Content -LiteralPath $taskConfigPath -Encoding UTF8
$taskLocal = Get-RecorderSettings -ConfigPath $taskConfigPath
if ($taskLocal.Mode -ne 'local' -or $taskLocal.ServerUrl -ne 'http://127.0.0.1:18487/recorder/') { throw 'Local recording requires a server.' }
Write-Output 'Desktop settings: remote validation, backward compatibility and server-free local configuration passed.'
