本指南用于可选的远程服务器模式。仅本机录制与直接上传请从 [README](../README.md) 开始。

# 配置与部署

## 服务结构

Chrome/Safari 通过 HTTPS 访问 `/recorder/`，Nginx 转发到本机 FastAPI。服务在独立 Linux 用户下运行，SQLite 和媒体位于专用数据目录。后台为单进程、单媒体工作线程；启动多 Uvicorn worker 或多个实例共享同一数据目录，会破坏当前的进程内队列锁。

部署目录示例：`/opt/daigui-recorder/server`；数据目录示例：`/var/lib/daigui-recorder`。这两个目录独立管理。

## 安装基础服务

准备 Python 3.11/3.12、Git、Nginx 和有效 HTTPS 域名。以下为 Linux 示例，先确认目录没有在用的安装：

```bash
sudo git clone https://github.com/FaYuaner/record-to-youtube.git /opt/daigui-recorder
cd /opt/daigui-recorder
sudo python3 -m venv venv
sudo venv/bin/python -m pip install --require-hashes -r server/requirements.lock
sudo useradd --system --home /var/lib/daigui-recorder --create-home --shell /usr/sbin/nologin daigui-recorder
sudo install -d -m 0700 -o daigui-recorder -g daigui-recorder /etc/daigui-recorder
sudo install -m 0600 -o daigui-recorder -g daigui-recorder .env.example /etc/daigui-recorder/recorder.env
```

已有服务用户或安装目录时沿用并核对权限，不重复创建或覆盖。按 [首次配置](CONFIGURATION.md) 填写该环境文件；把 `RECORDER_DATA_DIR` 改为 `/var/lib/daigui-recorder`，Google 凭据另存于受保护文件。配置完成后再安装下方 systemd 与 Nginx 设置并启动服务。

## 环境变量

以根目录 `.env.example` 为配置模板，使用部署者自己的服务器、Google OAuth 应用、账号、频道和 AI 凭据。生产环境可采用 `/etc/daigui-recorder/recorder.env`，权限设为 `0600`，只授予服务用户需要的读取权限。`RECORDER_BASE_URL`、`OWNER_EMAIL` 和 `OWNER_CHANNEL_ID` 是必填项，缺失时服务不会启动工作线程或开始 OAuth。

| 配置 | 用途 |
| --- | --- |
| `RECORDER_BASE_URL` | 自己部署的外部 HTTPS 地址，包含服务路径（如 `/recorder`）；决定 Cookie 路径、Origin 和 OAuth 回调 |
| `RECORDER_DATA_DIR` | SQLite、密钥与任务文件的持久目录，生产应使用绝对路径 |
| `OWNER_EMAIL`、`OWNER_CHANNEL_ID` | 唯一允许登录和上传的 Google 账号及 YouTube 频道 |
| `GOOGLE_CLIENT_SECRET_FILE` | 仓库外的 Google OAuth Web 应用凭据 JSON |
| `MAX_UPLOAD_BYTES` | 单个原片的大小上限，示例为 5 GiB |
| `MIN_FREE_BYTES` | 保留空闲磁盘空间，示例为 2 GiB；另有处理和在途任务空间检查 |
| `RECORDER_WORKER_ENABLED` | 生产设 `true`，隔离开发设 `false` |
| `ALLOW_PUBLIC_PUBLISH` | 是否允许请求公开上传；实际可见范围仍以 YouTube 返回状态为准 |
| `DAIGUI_WHISPER_MODEL` | 已安装的 faster-whisper/CTranslate2 模型路径 |
| `DAIGUI_TEXT_*` | 自动文案服务及服务端 API 密钥 |

Google 回调 URI 必须与 `${RECORDER_BASE_URL}/api/oauth/callback` 完全一致。为所用 Google 项目启用 YouTube Data API v3，并按其审核状态和配额使用。不能仅凭服务里的公开开关判断 Google 已批准公开发布。

`token.key` 和 `session.key` 在首次初始化时生成。备份时需与数据库一起保存；更换或丢失 `token.key` 后无法解密原有授权，替换 `session.key` 会使既有浏览器登录失效。不要把这些文件放入 Git。

## 网络与媒体依赖

安装两份 requirements、系统级 FFmpeg/ffprobe 和离线 ASR 模型。默认不允许请求自动下载模型。外部 API 的连接失败会保留任务和已验证成片，以便恢复。

若网络需要代理，显式配置 `GOOGLE_HTTP_PROXY` 和 `PROXY_URL`。普通环境保持 `PROXY_MANAGED=0`，使用自己已运行的固定代理。

Linux 按需代理使用 `PROXY_MANAGED=1`，必须填写自己的 `PROXY_SERVICE_NAME`、`PROXY_URL` 和绝对路径 `PROXY_LOCK_FILE`，并配置受限 sudo 权限与锁文件写入权限。就绪检查使用该代理地址的主机和端口；缺少配置时拒绝启停服务。`DIRECT_API_HOSTS` 可指定需要直连的 API 主机名，默认留空。

媒体处理使用 `python -m pip install --require-hashes -r server/media-requirements.lock` 安装固定依赖，并安装系统包 FFmpeg（包含 ffprobe）。预先准备 faster-whisper 模型后，将 `DAIGUI_WHISPER_MODEL` 填为本地模型目录或缓存中已有的模型名称。默认不自动下载；准备过程的体积、耗时及模型许可按所选模型确定。

## 服务启动示例

```ini
[Unit]
Description=Record to YouTube
After=network.target

[Service]
Type=simple
User=daigui-recorder
Group=daigui-recorder
WorkingDirectory=/opt/daigui-recorder/server
EnvironmentFile=/etc/daigui-recorder/recorder.env
ExecStart=/opt/daigui-recorder/venv/bin/uvicorn app:app --host 127.0.0.1 --port 18487 --workers 1 --no-access-log
Restart=on-failure
RestartSec=5
UMask=0077
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/daigui-recorder

[Install]
WantedBy=multi-user.target
```

按需代理部署还需要补充锁文件写入权限和仅允许自己指定服务启停的 sudo 规则。模型和凭据路径必须允许此服务用户访问。

Nginx 转发应保留原始 `/recorder/` 路径，并允许单次约 8 MiB 的分片请求：

```nginx
location = /recorder { return 308 /recorder/; }
location ^~ /recorder/ {
    proxy_pass http://127.0.0.1:18487;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 9m;
    proxy_request_buffering off;
    proxy_buffering off;
    proxy_read_timeout 240s;
}
```

使用有效 HTTPS 证书。避免记录 OAuth 回调查询参数、授权头和敏感请求正文。健康检查为 `/recorder/api/health`；它不能替代一次实际录制和上传验证。

## 维护

部署前确认没有进行中的任务，备份代码、受保护的配置、数据库和密钥，并测试可恢复性。保留数据目录，不用 Git 管理它。恢复数据库时遵循 SQLite WAL 的一致性备份方法，不能只随意复制一个正在写入的 `.sqlite3` 文件。

浏览器登录最长保留约一年，后台每天复核已有 Google 授权；API 身份及上传记录还受 29 天保留规则影响。Google 撤销授权时需重新连接。成功处理后的服务器媒体会清理；设置保留原片的任务除外。手机原片仍需主动下载备份，浏览器存储不能作为唯一档案。
