# Daigui Recorder

A self-hosted workflow for recording talking-head videos, trimming long pauses, transcribing speech, preparing metadata, and uploading to your own YouTube channel. [中文说明](README.md).

Record in your browser or open the workspace through the Windows launcher. Save the original locally, then choose whether to send it to your server for processing. Each deployment is restricted to its configured Google account and YouTube channel.

## Features

- Camera and microphone preview, quality/orientation settings, and background segmentation.
- Local recording recovery, chunked transfers, and resumable uploads.
- Pause trimming and local speech transcription.
- Existing titles and descriptions are preserved; blank fields can be generated in Traditional Chinese from the transcript.
- Video preview, metadata editing, and progress for server transfers and YouTube processing.
- Simplified Chinese, Traditional Chinese, and English UI; optional Windows recording bar.

## Setup

Use Python 3.11 or 3.12. In Windows PowerShell:

```powershell
git clone https://github.com/almustafadaigui-creator/daigui-recorder.git
cd daigui-recorder
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r server/requirements.txt
Copy-Item .env.example .env
```

Set your own `RECORDER_BASE_URL`, `OWNER_EMAIL`, `OWNER_CHANNEL_ID`, and Google OAuth credentials. The URL must use HTTPS and include a service path, such as `https://recorder.example.com/recorder`. Register `<RECORDER_BASE_URL>/api/oauth/callback` as the OAuth Web application's redirect URI.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --app-dir server --env-file .env --host 127.0.0.1 --port 18487 --workers 1
```

Put this service behind your HTTPS reverse proxy. Missing required settings prevent startup. See the [configuration guide](docs/CONFIGURATION.md) and [Linux deployment guide](docs/DEPLOYMENT.md).

## First recording

Open your HTTPS workspace, connect the configured Google account/channel, and allow camera/microphone access. Record a short clip and choose to keep it on your device. Verify the original file was saved.

For server processing, install the media requirements, FFmpeg/ffprobe, and a local ASR model. Configure your own text API if you want generated metadata, then enable `RECORDER_WORKER_ENABLED=true`. Send a short recording to the server and inspect the resulting video and metadata.

New installations default to private visibility and manual YouTube uploads. Automatic uploads and public visibility are explicit settings. Confirm your own YouTube API publishing eligibility before enabling public uploads.

## Windows launcher

Install Chrome, copy `desktop/recorder.config.example.json` to `desktop/recorder.config.json`, set your own `serverUrl`, and open `desktop/Start-Recorder.vbs`. Blank browser fields use Chrome detection and a separate profile for the current Windows user.

## Limits and development

Run one server process and one media worker per data directory. Each instance supports one configured account/channel. Mobile recording must remain in the foreground. Generated metadata is Traditional Chinese; speech recognition language is configurable with `DAIGUI_ASR_LANGUAGE`.

```powershell
python -B tests/run_offline.py
powershell -NoProfile -File tests/desktop_settings_tests.ps1
```

See [contributing](CONTRIBUTING.md), [test scope](docs/TESTING.md), and [third-party notices](docs/THIRD_PARTY_NOTICES.md).
