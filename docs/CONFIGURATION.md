# 本机配置

默认本机模式无需远程服务器。先按 README 安装依赖，通过桌面入口启动；Google 尚未连接时也可以录制并保存原片。

复制 `desktop/local.env.example` 为 `desktop/local.env`。该文件只用于当前安装。

| 配置 | 用途 |
| --- | --- |
| `GOOGLE_CLIENT_SECRET_FILE` | 自己的 Google **Desktop app** OAuth JSON 绝对路径；连接 YouTube 必填 |
| `GOOGLE_HTTP_PROXY` | 当前网络需要时，显式填写自己的 Google 代理地址 |
| `RECORDER_PROCESSING_MODE` | 初始处理方式：`direct`、`external` 或 `builtin` |
| `RECORDER_PROCESSOR_CONFIG` | 自定义工具的私人 JSON 配置路径 |
| `OWNER_EMAIL` / `OWNER_CHANNEL_ID` | 可选的预先允许账号与频道；留空则绑定首次授权的唯一所选频道 |
| `RECORDER_LOCAL_PORT` | 本机端口，默认 18487；仅绑定 127.0.0.1 |
| `ALLOW_PUBLIC_PUBLISH` | 确认 API 发布资格后，是否允许选择公开上传；默认 false |

Google 控制台步骤：启用 YouTube Data API v3 → 配置 Google Auth Platform 的 Branding / Audience → 测试阶段添加自己的账号 → Clients 创建 **Desktop app** → 下载 JSON。桌面授权使用 HTTP 回环回调和 PKCE，不能直接使用旧 Web 应用 JSON。

录制与上传端点仅接受桌面入口的本机访问凭据和页面会话。OAuth 令牌加密保存在本机数据目录。使用同一安装的首次账号与频道绑定；要管理独立的另一套账号和记录，应使用独立数据目录与浏览器目录。

配置变化后重启服务。使用自己的 Google 测试项目时，刷新令牌可能受测试期限限制；上传到未审核 API 项目的影片也可能被限制为私人。工具按实际平台返回显示结果。

[剪辑工具与 Skill 配置](CUSTOM_EDITING.md)

---

# 首次配置

先准备自己的 HTTPS 服务地址、Google 账号、YouTube 频道和 Google Cloud 项目，再决定是否使用自动文案服务。

## 服务与账号

复制根目录 `.env.example` 为本机 `.env`，生产环境也可使用部署指南中的受保护环境文件。

| 配置 | 填写方法 |
| --- | --- |
| `RECORDER_BASE_URL` | 自己工作台的完整 HTTPS 地址，包含服务路径，不带查询参数；末尾无需 `/` |
| `OWNER_EMAIL` | 允许登录的 Google 账号完整邮箱 |
| `OWNER_CHANNEL_ID` | 该账号有权操作的 YouTube 频道 ID |
| `RECORDER_DATA_DIR` | 该实例专用的数据目录；生产使用绝对路径 |

频道 ID 可在 [YouTube 高级账号设置](https://www.youtube.com/account_advanced) 查看。使用品牌频道时，确认所选频道与该 ID 一致。每个部署仅允许配置的账号和频道；另外的创作者部署自己的实例。

## Google OAuth

1. 在自己的 [Google Cloud Console](https://console.cloud.google.com/) 创建或选择项目，启用 **YouTube Data API v3**。
2. 配置 OAuth 同意屏幕，填写当前实例的应用名称、域名和有效隐私政策/条款链接；测试模式下把自己的账号加入测试用户。
3. 创建 **Web application** 类型的 OAuth 客户端，添加与 `${RECORDER_BASE_URL}/api/oauth/callback` 完全一致的已授权重定向 URI。
4. 下载客户端 JSON，将它保存在仓库之外的受保护路径，在 `GOOGLE_CLIENT_SECRET_FILE` 填写该路径。Linux 文件权限设为 `0600`，服务用户需要读取权限。

也可通过环境变量提供 `GOOGLE_CLIENT_ID` 和 `GOOGLE_CLIENT_SECRET`。不要把实际凭据复制进示例文件、Git 提交或公开问题报告。官方流程见 [Web server OAuth 指南](https://developers.google.com/identity/protocols/oauth2/web-server)。

首次连接时，在工作台点击 Google 登录，选择配置的账号并授权所需范围。账号或频道不匹配时会拒绝连接。平台的测试模式、授权有效期、审核与配额仍适用。

## 转录与自动文案

服务器转录需要 `server/media_requirements.txt`、FFmpeg/ffprobe 和准备好的 faster-whisper 模型。`DAIGUI_WHISPER_MODEL` 可以填写已经准备好的模型名称或绝对路径；默认禁止自动下载。模型应遵循自己的许可和硬件要求。

`DAIGUI_ASR_LANGUAGE=zh` 用于中文口播；留空可由模型检测语言。自动生成的标题与简介当前采用繁体中文；自行填写的内容原样保留。

自动文案服务按自己的选择填写：

| 配置 | 含义 |
| --- | --- |
| `DAIGUI_TEXT_PROVIDER` | `openai`、`anthropic` 或 `gemini` 协议 |
| `DAIGUI_TEXT_MODEL` | 所选服务实际支持的模型 ID |
| `DAIGUI_TEXT_API_KEY` | 自己的服务端 API 凭据 |
| `DAIGUI_TEXT_BASE_URL` | 留空使用所选协议的官方端点；兼容服务填写自己的 HTTPS API 基础地址 |

OpenAI 兼容基础地址应包含协议要求的 `/v1`；Anthropic 支持带或不带 `/v1` 的基础地址；Gemini 使用服务支持的版本路径。根据服务商文档核对，不把普通网页地址当作 API 地址。

两项文案已填写时无需自动生成。缺少文案服务配置或请求失败时，保留成片和转录，并提示填写文案或修复配置后重试。

## 首次启用

配置完成后先保持 `RECORDER_WORKER_ENABLED=false`、`ALLOW_PUBLIC_PUBLISH=false`，确认 HTTPS、登录和本机原片保存。媒体依赖准备好后，再开启工作线程处理一段短片。

自动上传 YouTube 在工作台主动开启。公开上传还需设置 `ALLOW_PUBLIC_PUBLISH=true`，并确认 Google 项目具备相应资格；最终可见状态以 YouTube 返回结果为准。

## 网络

可直接访问外部 API 时保持 `PROXY_MANAGED=0`，代理地址留空。需要代理时配置自己已有的 `GOOGLE_HTTP_PROXY` 与 `PROXY_URL`，不依赖其他部署的服务。

Linux 按需代理需要显式填写自己的 `PROXY_SERVICE_NAME`、`PROXY_URL` 和绝对路径 `PROXY_LOCK_FILE`，另行配置最小必要的服务启停权限。`DIRECT_API_HOSTS` 可填写明确需要直连的 API 主机名，以逗号分隔；默认不为某一家服务商特殊选择路由。
