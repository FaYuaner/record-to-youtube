# 第三方依赖

`server/static/vendor/vision` 包含 MediaPipe Tasks Vision `0.10.32` 的网页运行依赖，随仓库保留 Apache License 2.0 原文及 `provenance.json`。其中 WebAssembly 文件用于背景分割，不能在打包时遗漏。

- npm 包：[MediaPipe Tasks Vision](https://www.npmjs.com/package/@mediapipe/tasks-vision)
- 源项目：[google-ai-edge/mediapipe](https://github.com/google-ai-edge/mediapipe)
- 原许可证：`server/static/vendor/vision/LICENSE`
- 模型与安装包摘要：`server/static/vendor/vision/provenance.json`

Python 依赖由两份 requirements 描述，各自遵守其许可证。FFmpeg 和 ASR 模型需要另行安装，不随本仓库分发。若将仓库改为公开或发布安装包，应先复核全部再分发条件、模型许可、品牌资源及项目本身的许可安排。
