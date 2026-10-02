# 第三方依赖

`server/static/vendor/vision` 包含 MediaPipe Tasks Vision `0.10.32` 的网页运行依赖，随仓库保留 Apache License 2.0 原文及 `provenance.json`。其中 WebAssembly 文件用于背景分割，不能在打包时遗漏。

- npm 包：[MediaPipe Tasks Vision](https://www.npmjs.com/package/@mediapipe/tasks-vision)
- 源项目：[google-ai-edge/mediapipe](https://github.com/google-ai-edge/mediapipe)
- 原许可证：`server/static/vendor/vision/LICENSE`
- 模型与安装包摘要：`server/static/vendor/vision/provenance.json`

本项目原创代码与文档采用 [MIT 许可证](../LICENSE)。MediaPipe 等第三方组件继续遵循各自许可证，项目的 MIT 许可证不替代第三方许可。

Python 依赖由两份 requirements 描述，各自遵守其许可证。FFmpeg 和 ASR 模型需要另行安装，不随本仓库分发。制作安装包时，须遵守实际包含的组件与模型的再分发条件。
