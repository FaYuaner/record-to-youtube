# 安装、升级与卸载

当前版本是 0.1.0 公开预览版，支持 Windows 与 Python 3.11/3.12，需要 Google Chrome。源码包包含程序与网页资源，首次安装仍需下载 Python 依赖。

## 下载指定版本

在 [版本发布页](https://github.com/FaYuaner/record-to-youtube/releases) 下载源码 ZIP，解压到一个新的固定目录。也可以使用 Git：

```powershell
git clone --branch v0.1.0 https://github.com/FaYuaner/record-to-youtube.git
cd record-to-youtube
powershell -NoProfile -ExecutionPolicy Bypass -File desktop/Install-Local.ps1
```

使用 ZIP 时，在解压后的项目目录运行同一个安装命令。安装器检查 Python 版本，创建 `.venv`，校验依赖文件哈希并创建桌面入口。指定 Python 时可使用 `-PythonPath`；开发验证时使用 `-SkipShortcut` 避免创建桌面快捷方式。

双击 `desktop/Start-Recorder.vbs`，允许使用摄像头与麦克风，录制几秒后点击结束，检查列表中的原片与预览。尚未连接 YouTube 时不会上传；连接自己的频道后，沿用开始录制时的上传设置。授权配置见 [配置指南](CONFIGURATION.md)。

## 升级前保留资料

先结束录制，确认原片已完整保存。等待正在上传的任务完成或暂停它，再关闭录制网页和本项目的本机服务。任务管理器中可以查看命令行，确认目标 Python 进程对应当前安装的 `server\local_runner.py` 后结束该进程。

将以下资料复制到项目目录之外的私人备份位置：

- 如已创建：`desktop/local.env`、`desktop/recorder.config.json` 和自己的剪辑处理配置。
- Google OAuth JSON 及其他由配置指定的私人文件。
- 本机队列与授权数据，默认为 `%LOCALAPPDATA%\DaiguiRecorder\data`；设置过 `RECORDER_DATA_DIR` 时备份实际目录。
- 另选的原片保存目录。浏览器恢复副本不能替代原片备份。

这些文件保留在个人环境中，升级包无需包含它们。

## Git 安装升级

在当前安装目录执行 `git status --short`。若有自己的源码修改，先保存或提交，处理完后再更新；私人配置由 `.gitignore` 排除。

```powershell
git fetch origin --tags
git switch main
git pull --ff-only origin main
powershell -NoProfile -ExecutionPolicy Bypass -File desktop/Install-Local.ps1
```

检查 `VERSION` 和 [更新记录](../CHANGELOG.md)，再打开桌面入口。安装器保留现有配置与数据，不会自动清理旧依赖。

## ZIP 安装升级

新版本解压到新目录，保留旧安装。只复制自己的配置文件到相应位置，OAuth JSON 和数据目录继续使用原路径。运行新目录的安装器；已有同名桌面快捷方式指向旧目录时，安装器会保留它，可在快捷方式属性中手动改为新目录的 `desktop/Start-Recorder.vbs`，并修改“起始位置”和图标。

启动前确保旧服务已停止。先检查设置、原片和历史任务，再开始新录制。

## 回退

保留升级前的程序和数据备份。Git 安装可执行 `git switch --detach v0.1.0` 返回此版本，再运行安装器；ZIP 安装使用保留的旧目录。回退前停止当前服务。

本版没有新增数据库迁移。后续版本如变更数据结构，按对应更新说明处理，不直接让旧程序读取新版数据。

## 卸载

先结束录制和上传，并停止本项目服务。手动删除对应桌面快捷方式与程序目录即可。录像、队列、浏览器资料和 OAuth 文件由你决定保留或清理，程序目录删除不会自动撤销 Google 授权；需要撤销时，在 Google 账号的第三方连接设置中移除该应用。

## 常见问题

| 现象 | 处理方式 |
| --- | --- |
| 找不到 Python 或版本不支持 | 安装 Python 3.11/3.12，或用 `-PythonPath` 指定解释器 |
| 依赖安装失败 | 检查网络和系统时间后重试；哈希不符时重新获取官方版本，保留校验 |
| 本机端口被占用 | 先确认旧服务是否仍在运行；需要改端口时设置 `RECORDER_LOCAL_PORT` 后重启 |
| 无法打开摄像头或麦克风 | 检查 Chrome 网站权限、Windows 隐私设置及其他程序的设备占用 |
| 连接 Google 提示缺少 OAuth JSON | 配置自己的桌面 OAuth JSON 路径，再重启服务 |
| YouTube 上传仍为私人 | 检查自己的 API 项目审核、OAuth 设置及平台返回结果 |
| 服务已停止但队列未完成 | 重新启动相同配置与数据目录，查看队列状态并按提示重试 |

仍需帮助时使用 [问题报告](https://github.com/FaYuaner/record-to-youtube/issues/new/choose)，提供版本、系统、复现步骤与经过脱敏的错误信息。
