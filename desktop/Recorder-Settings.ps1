function Get-RecorderSettings {
    param([string] $ConfigPath = (Join-Path $PSScriptRoot 'recorder.config.json'))

    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        throw '请先将 recorder.config.example.json 复制为 recorder.config.json，并填写自己的 serverUrl。'
    }
    try {
        $settings = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        throw '录制配置无法读取，请检查 recorder.config.json 的 JSON 格式。'
    }
    $serverUrl = ([string] $settings.serverUrl).Trim()
    [Uri] $serverUri = $null
    if (-not [Uri]::TryCreate($serverUrl, [UriKind]::Absolute, [ref] $serverUri) -or
        $serverUri.Scheme -ne 'https' -or -not $serverUri.Host -or
        $serverUri.UserInfo -or $serverUri.Query -or $serverUri.Fragment -or
        $serverUri.AbsolutePath -eq '/') {
        throw '请在 serverUrl 填写自己录制工作台的完整 HTTPS 地址和服务路径。'
    }

    $chromePath = ([string] $settings.chromePath).Trim()
    if (-not $chromePath) {
        foreach ($baseDirectory in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
            if (-not $baseDirectory) { continue }
            $candidate = Join-Path $baseDirectory 'Google\Chrome\Application\chrome.exe'
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $chromePath = $candidate; break }
        }
    }
    if (-not $chromePath -or -not (Test-Path -LiteralPath $chromePath -PathType Leaf)) {
        throw '找不到 Google Chrome，请先安装 Chrome，或在 chromePath 中填写安装路径。'
    }
    if (-not [IO.Path]::IsPathRooted($chromePath)) { throw 'chromePath 必须使用绝对路径。' }

    $profileDirectory = ([string] $settings.profileDirectory).Trim()
    if (-not $profileDirectory) {
        if (-not $env:LOCALAPPDATA) { throw '无法找到用户资料目录，请配置 profileDirectory。' }
        $profileDirectory = Join-Path $env:LOCALAPPDATA 'DaiguiRecorder\ChromeProfile'
    }
    if (-not [IO.Path]::IsPathRooted($profileDirectory)) { throw 'profileDirectory 必须使用绝对路径。' }
    if ((Test-Path -LiteralPath $profileDirectory) -and -not (Test-Path -LiteralPath $profileDirectory -PathType Container)) {
        throw 'profileDirectory 指向了一个文件，请选择浏览器资料文件夹。'
    }

    [PSCustomObject] @{
        ServerUrl = $serverUri.AbsoluteUri.TrimEnd('/') + '/'
        ChromePath = [IO.Path]::GetFullPath($chromePath)
        ProfileDirectory = [IO.Path]::GetFullPath($profileDirectory)
    }
}
