# Daigui Recorder · 今日录制

自部署的口播录制与 YouTube 视频制作工具：在浏览器录下声音和画面，将原片保存到本机，再由自己的服务器剪辑停顿、转录内容、整理文案，并上传到自己授权的频道。

Self-hosted video recording, pause trimming, transcription, and YouTube uploads. [English guide](README.en.md).

## 能做什么

- 录制前预览画面和音量，选择摄像头、麦克风、画质与方向；支持背景分割。
- 分段保存恢复副本，原片可留在本机，也可经确认后传到服务器；支持断线续传。
- 缩短较长的无声停顿，保留短停顿和人声边界，生成逐字稿。
- 保留已填写的标题与简介，仅为空白字段生成忠实于口播的繁体中文文案。
- 预览成片、修改文案，按选择上传 YouTube，并显示实际处理状态。
- 支持简体中文、繁体中文与英文界面；Windows 提供桌面入口和录制条。

适合希望自行管理录制资料、处理流程与频道授权的创作者。每个服务实例对应一个 Google 账号与一个 YouTube 频道；不同创作者分别配置自己的实例。

## 从哪里开始

| 需求 | 入口 |
| --- | --- |
| 首次配置服务器、账号与 API | [配置指南](docs/CONFIGURATION.md) |
| 在 Linux 服务器运行 | [部署指南](docs/DEPLOYMENT.md) |
| 启动 Windows 桌面入口 | [桌面使用说明](desktop/使用說明.md) |
| 修改源码与提交贡献 | [开发与贡献](CONTRIBUTING.md) |

### 1. 获取源码并安装基础依赖

建议 Python 3.11 或 3.12。在 Windows PowerShell 中执行：

```powershell
git clone https://github.com/almustafadaigui-creator/daigui-recorder.git
cd daigui-recorder
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r server/requirements.txt
Copy-Item .env.example .env
```

Linux 部署步骤见 [部署指南](docs/DEPLOYMENT.md)。浏览器录制和 Google 登录需要有效 HTTPS 入口。

### 2. 配置自己的服务

在 `.env` 填写自己的 `RECORDER_BASE_URL`、`OWNER_EMAIL`、`OWNER_CHANNEL_ID` 和 Google OAuth 凭据。地址需包含服务路径，例如 `https://recorder.example.com/recorder`。配置与取得方法见 [配置指南](docs/CONFIGURATION.md)。

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --app-dir server --env-file .env --host 127.0.0.1 --port 18487 --workers 1
```

用 HTTPS 反向代理转发到此服务。三个必填配置缺失时，服务会指出缺少的字段；Google 登录仅接受该实例配置的账号和频道。

### 3. 完成第一次录制

打开自己配置的 HTTPS 地址，连接自己的 Google 账号与 YouTube 频道，允许摄像头和麦克风。录一段短口播，结束后选择“留在此设备”，检查原片已保存。

要使用服务器制作，先安装 [媒体依赖](docs/DEPLOYMENT.md#网络与媒体依赖)、准备转录模型，填写所需 AI 配置，并设置 `RECORDER_WORKER_ENABLED=true`。然后录制或导入一个短片，确认上传服务器；列表会显示处理进度，完成后可预览成片并检查文案。

首次使用默认关闭自动上传 YouTube，可见范围为私人。确认成片后再上传；需要自动上传或公开发布时，在设置中主动启用，并核实自己的 YouTube API 发布资格。后台处理线程也默认关闭，部署就绪后再开启。

## Windows 桌面入口

安装 Google Chrome。复制 `desktop/recorder.config.example.json` 为 `desktop/recorder.config.json`，填写自己的 `serverUrl`，双击 `desktop/Start-Recorder.vbs`。

`chromePath` 留空时检测已安装的 Chrome；`profileDirectory` 留空时创建当前用户的独立浏览器目录。未配置有效 HTTPS 地址时，启动器会显示配置提示。桌面入口使用 Windows PowerShell、WinForms 和 UI Automation；也可直接在浏览器中打开工作台。

## 使用条件与边界

- 服务使用单进程、单媒体工作线程；不能让多个进程共享同一数据目录。
- 录制需要浏览器权限。手机请保持页面在前台；长录制能力受设备内存、浏览器存储与服务器空间影响。
- AI 文案需要自己配置的模型服务；语音转录使用服务器本地模型。两项文案均已填写时无需生成。
- OAuth、AI API 与 YouTube 上传受各平台审核、额度和费用规则约束。
- YouTube 确认处理成功后，服务器可清理临时影音；设置保留原片的任务除外。重要原片请自行备份。

## 开发检查

```powershell
python -B tests/run_offline.py
powershell -NoProfile -File tests/desktop_settings_tests.ps1
```

默认测试使用隔离数据和模拟外部请求。媒体集成检查与测试范围见 [测试说明](docs/TESTING.md)。

## 依赖与许可

MediaPipe 的必要运行文件及 Apache License 2.0 随仓库保留。FFmpeg 和 ASR 模型另行安装，遵循各自许可；详见 [第三方依赖](docs/THIRD_PARTY_NOTICES.md)。
