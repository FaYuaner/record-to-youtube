# Daigui Recorder

Record video on Windows, keep the original locally, and automatically upload to your own YouTube channel. Upload original footage directly, or optionally edit it with your own tool or Codex Skill. A remote server is optional.

[中文说明](README.md)

## Quick start

Install Google Chrome and Python 3.11 or 3.12 on Windows.

```powershell
git clone https://github.com/almustafadaigui-creator/daigui-recorder-local.git
cd daigui-recorder-local
powershell -NoProfile -ExecutionPolicy Bypass -File desktop/Install-Local.ps1
```

Double-click `desktop/Start-Recorder.vbs`. The launcher starts a private loopback workspace. Check your camera and microphone, choose a save folder, and make a short recording. Recording needs neither a cloud server nor an AI provider. Until YouTube is connected, originals stay on this device.

## YouTube connection

Create your own Google Cloud project and enable YouTube Data API v3. Configure Google Auth Platform, add your account as a test user when appropriate, and create a **Desktop app** OAuth client. Download its JSON into your private directory.

Copy `desktop/local.env.example` to `desktop/local.env` and set `GOOGLE_CLIENT_SECRET_FILE` to that JSON's absolute path. Restart the local service, open the desktop launcher and connect your Google account. The first authorized account and selected channel are bound to this installation.

Automatic upload is initially enabled, with private visibility. You can turn it off and preview before uploading. Actual visibility follows YouTube's response; testing-mode OAuth lifetimes and unaudited API-project upload restrictions still apply.

## Optional editing

Choose direct original upload, custom editing / Skill, or the built-in editing and transcription pipeline. Direct upload requires no FFmpeg or AI service; empty titles use the recording filename and descriptions may be empty.

The [custom tool protocol](docs/CUSTOM_EDITING.md) supports your own CLI or editing workflow. A [Codex adapter](examples/codex_skill_adapter.py) executes your own Skill using your authenticated CLI, and a [FFmpeg example](examples/ffmpeg_tool.py) demonstrates the protocol without AI. Editing requires ffprobe for output validation. Failed or timed-out tools retain the original and stop publication.

The local queue can continue after closing the browser; keep the computer and service running. Uploads use resumable YouTube sessions, with pause, resume and progress feedback. Local originals and finished videos are retained.

## Optional remote deployment

Use `mode: remote` and your own HTTPS `serverUrl` in desktop configuration. Configure your own account, channel and Web OAuth client for that service. See [configuration](docs/CONFIGURATION.md), [deployment](docs/DEPLOYMENT.md), and [contributing](CONTRIBUTING.md).

## Checks and dependencies

```powershell
python -B tests/run_offline.py
powershell -NoProfile -File tests/desktop_settings_tests.ps1
```

Offline checks isolate runtime data and simulate external requests. MediaPipe resources retain Apache License 2.0 notices; see [third-party notices](docs/THIRD_PARTY_NOTICES.md). Platform permissions, quotas and provider charges apply to your own accounts.
