$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class DaiguiRecorderWindow {
    public delegate bool EnumProc(IntPtr window, IntPtr parameter);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc callback, IntPtr parameter);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr window, StringBuilder text, int count);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetClassName(IntPtr window, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] private static extern IntPtr GetAncestor(IntPtr window, uint flags);
    public static IntPtr[] Windows() {
        var result = new List<IntPtr>();
        EnumWindows((window, parameter) => { result.Add(window); return true; }, IntPtr.Zero);
        return result.ToArray();
    }
    public static bool IsRecorder(IntPtr window) {
        if (!IsWindowVisible(window) || GetAncestor(window, 2) != window) return false;
        var title = new StringBuilder(1024);
        var className = new StringBuilder(256);
        GetWindowText(window, title, title.Capacity);
        GetClassName(window, className, className.Capacity);
        return className.ToString().StartsWith("Chrome_WidgetWin_", StringComparison.Ordinal)
            && title.ToString().IndexOf("Record to YouTube", StringComparison.OrdinalIgnoreCase) >= 0;
    }
}
'@

function Show-RecorderNotice([string] $Text, [bool] $IsError = $false) {
    $notice = New-Object System.Windows.Forms.NotifyIcon
    try {
        $notice.Icon = if ($IsError) { [System.Drawing.SystemIcons]::Warning } else { [System.Drawing.SystemIcons]::Information }
        $notice.Visible = $true
        $notice.BalloonTipTitle = 'Record to YouTube'
        $notice.BalloonTipText = $Text
        $notice.ShowBalloonTip(5000)
        Start-Sleep -Seconds 5
    } finally { $notice.Dispose() }
}

try {
    . (Join-Path $PSScriptRoot 'Recorder-Settings.ps1')
    $recorderSettings = Get-RecorderSettings
    $recorderUrl = $recorderSettings.ServerUrl
    if ($recorderSettings.Mode -eq 'local') {
        . (Join-Path $PSScriptRoot 'Local-Service.ps1')
        $recorderUrl = Start-LocalRecorder -Port $recorderSettings.LocalPort
    }
    $recorderProfile = $recorderSettings.ProfileDirectory
    $recorderChrome = $recorderSettings.ChromePath
    [void] [IO.Directory]::CreateDirectory($recorderProfile)

    $existingWindows = New-Object 'System.Collections.Generic.HashSet[System.IntPtr]'
    foreach ($window in [DaiguiRecorderWindow]::Windows()) { [void] $existingWindows.Add($window) }

    $arguments = @(
        ('--user-data-dir="' + $recorderProfile + '"'),
        '--profile-directory=Default',
        '--new-window',
        ('--app="' + $recorderUrl + '"'),
        '--window-size=560,800'
    )
    Start-Process -FilePath $recorderChrome -ArgumentList $arguments

    $deadline = [DateTime]::UtcNow.AddSeconds(25)
    $placed = $false
    do {
        foreach ($window in [DaiguiRecorderWindow]::Windows()) {
            if ($existingWindows.Contains($window) -or -not [DaiguiRecorderWindow]::IsRecorder($window)) { continue }
            [uint32] $windowProcessId = 0
            [void] [DaiguiRecorderWindow]::GetWindowThreadProcessId($window, [ref] $windowProcessId)
            $windowProcess = Get-Process -Id $windowProcessId -ErrorAction SilentlyContinue
            if (-not $windowProcess -or $windowProcess.ProcessName -ne 'chrome') { continue }
            $screen = [System.Windows.Forms.Screen]::FromHandle($window).WorkingArea
            $windowWidth = [Math]::Min(560, [Math]::Max(340, $screen.Width - 32))
            $windowHeight = [Math]::Min(850, [Math]::Max(440, $screen.Height - 40))
            $left = $screen.Right - $windowWidth - 16
            $top = $screen.Top + 20
            [void] [DaiguiRecorderWindow]::ShowWindow($window, 9)
            $placed = [DaiguiRecorderWindow]::SetWindowPos($window, [IntPtr] (-1), $left, $top, $windowWidth, $windowHeight, 0x0040)
            [void] [DaiguiRecorderWindow]::SetForegroundWindow($window)
            if ($placed) { break }
        }
        if (-not $placed) { Start-Sleep -Milliseconds 250 }
    } while (-not $placed -and [DateTime]::UtcNow -lt $deadline)

    if ($placed) {
        . (Join-Path $PSScriptRoot 'Recorder-Bar.ps1')
        Watch-RecorderBar -OwnerWindow $window
    }
    if (-not $placed) { Show-RecorderNotice '錄製頁面已開啟。若視窗未保持置頂，仍可在 Chrome 中正常錄製。' }
} catch {
    Show-RecorderNotice ('未能開啟錄製：' + $_.Exception.Message) $true
    exit 1
}
