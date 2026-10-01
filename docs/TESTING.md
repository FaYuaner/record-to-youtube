# 测试说明

在仓库根目录执行 `python -B tests/run_offline.py`。运行器分别启动测试进程，隔离数据目录，清空外部凭据配置并禁止实际网络连接。

测试包括部署配置缺失时拒绝启动、OAuth 使用部署者指定的回调和账号、其他账号无法读取录像、人声区间算法、标题与简介保留、会话访问限制、授权失效与重试、工作线程恢复和防止重复上传。测试里的公开发布开关只作用于临时进程，用于校验接口，未连接任何真实频道。

桌面配置检查运行 `powershell -NoProfile -File tests/desktop_settings_tests.ps1`，验证配置缺失、空地址、不安全的地址和用户自定义服务器配置。它不会启动 Chrome 或接入任何服务。

默认入口跳过三项会清理多个生成影音文件的测试：

- `test_cleanup_keeps_text_other_jobs_unknown_files_and_remote_reference`
- `test_cleanup_failure_is_visible_and_retry_does_not_upload_again`
- `test_youtube_processed_success_triggers_cleanup_and_correct_percent`

这些用例已随源码保留，适合在确认允许清理测试文件的独立环境运行。默认入口还跳过需要 FFmpeg 的 `MediaIntegrationTests`；仅运行 `TimelineTests`，避免把媒体集成检查误认为真实语音识别或发布验证。

测试产生的数据库、会话测试密钥与合成文件被 `.gitignore` 排除，不进入提交。运行器不清理测试目录。

长录制的内存、磁盘占用、手机切后台恢复、设备掉线、实际转录质量、YouTube API 审核状态与真实上传吞吐，应另外用专用测试素材做实际验证。本次上传检查不启动线上任务。
