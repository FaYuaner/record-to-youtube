# 自定义剪辑与 Skill

处理方式在“频道与发布”中选择。直接上传不运行剪辑；自定义剪辑运行使用者配置的命令，等待结果并验证成片；内置模式运行现有停顿剪辑、转录与文案流程。

## 用自己的 Codex Skill

先安装、登录 Codex CLI，并确认 FFmpeg / ffprobe 已加入 PATH。准备自己的剪辑 `SKILL.md`，说明想保留的表达、停顿处理、字幕和输出要求。

复制 `examples/processor.config.example.json` 到私人目录，填写真实的程序、适配器与 Skill 路径。示例中的 `python` 应使用已安装依赖的 Python；`command` 是参数数组，包含一个 `{request}`。

在 `desktop/local.env` 配置：

```dotenv
RECORDER_PROCESSOR_CONFIG=C:/your-private-settings/processor.config.json
```

重启本机服务，选择“自定义剪辑 / Skill”，保存设置。录制结束后，适配器在该任务的输出目录调用 `codex exec --sandbox workspace-write`，把输入请求与 Skill 交给你自己的 Codex 账号。工具应只完成剪辑，上传由录制器在结果通过验证后执行。相关模型调用使用你的账号和额度。

标题与简介已经填写时，录制器保留原文。留空字段可由处理器返回；处理器未提供标题时使用录制文件名，简介可为空。要根据口播提炼文案，Skill 应先真实读取或转录内容。

## 不用 AI 的工作示例

`examples/ffmpeg_tool.py` 将输入转码为 H.264 / AAC 的 MP4，示范完整协议。它本身不执行停顿剪辑。

```json
{
  "command": ["python", "C:/your-recorder/examples/ffmpeg_tool.py", "{request}"],
  "timeout_seconds": 3600
}
```

## 接入任何处理工具

录制器创建一个任务专用的输出目录，并调用配置的命令。`{request}` 替换为 UTF-8 JSON 的绝对路径。请求包含：

| 字段 | 含义 |
| --- | --- |
| `version` | 协议版本，当前为 1 |
| `job_id` | 当前录制任务编号 |
| `input_path` | 原片的工作副本 |
| `output_directory` | 此任务允许写入成片的目录 |
| `result_path` | 完成后写入的结果 JSON |
| `skill_path` | 可选的使用者 Skill 路径 |
| `metadata` | 使用者填写的标题与简介 |

工具完成后以退出码 0 退出，并在 `result_path` 写入：

```json
{
  "final_path": "final.mp4",
  "title": "视频标题",
  "description": "视频简介",
  "transcript": ""
}
```

`final_path` 可以是输出目录内的绝对路径或相对路径，必须是独立、非空的成片，不能返回输入副本或目录外文件。`title`、`description`、`transcript` 可省略，提供时必须是字符串。ffprobe 会验证视频流和有效时长。

处理器不得自行上传视频。工具失败、缺结果、超时或成片验证失败时，队列会显示原因，原片继续保留。重试前会清除上次结果文件，避免使用旧的成功结果；超时会停止此次工具的进程树。

自定义命令按当前用户权限运行，仅配置自己信任的工具。上传端的 Google OAuth 环境不会传给剪辑工具。若工具需要其他服务，应使用它自己的凭据和配置。
