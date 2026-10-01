# 开发与贡献

欢迎提交可复现的问题和针对具体行为的改进。请先说明触发条件、预期结果与实际结果；问题报告和测试夹具使用虚构账号、频道与示例域名，不粘贴凭据、私人录音或完整运行配置。

## 开发环境

使用 Python 3.11 或 3.12，安装 `server/requirements.txt`，按 [配置指南](docs/CONFIGURATION.md) 创建自己的本地配置。开发时保持工作线程和公开发布关闭。媒体处理需要额外安装 `server/media_requirements.txt`、FFmpeg/ffprobe 和转录模型。

代码入口：

- `server/app.py`：任务、上传、设置与访问控制。
- `server/auth.py`：Google OAuth、指定账号/频道验证和授权维护。
- `server/media_pipeline.py`：剪辑、转录与文案生成。
- `server/static/`：录制工作台、恢复流程与语言资源。
- `desktop/`：Windows 启动器和录制条。

## 提交前

在项目根目录运行：

```powershell
python -B tests/run_offline.py
powershell -NoProfile -File tests/desktop_settings_tests.ps1
```

离线测试不访问真实 Google/AI 服务。需要实测录制或发布时，使用自己的专用环境与素材；默认检查不会代为发布影片。具体测试范围见 [测试说明](docs/TESTING.md)。

PR 说明应包括具体问题、最终行为、实际验证和相关限制。涉及界面交互时，验证点击后的处理中、成功和失败反馈；涉及授权或数据归属时，验证拒绝错误账号及保持恢复能力。
