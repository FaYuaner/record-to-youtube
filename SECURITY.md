# 安全报告

请勿在公开 Issue、PR 或讨论中粘贴漏洞利用细节、OAuth 凭据、令牌、浏览器资料、私人录像、完整日志或运行配置。

仓库已启用 [私人漏洞报告](https://github.com/FaYuaner/record-to-youtube/security/advisories/new)。登录 GitHub 后，通过该入口向维护者提交漏洞描述、影响版本与最小复现步骤。

普通功能问题请使用问题报告表单，并先移除账号、频道、绝对私人路径和凭据。发现凭据已泄露时，应先在对应服务撤销或轮换；删除 Git 文件不会使旧凭据失效。

目前维护最新的公开预览版本。当前版本见 `VERSION`，更新见 `CHANGELOG.md`。

## 开发检查

提交前运行 `python tools/check_publication.py --root . --worktree`，发布前再运行 `--history`。项目特有的私人标识可使用仓库外的 `--markers-file` 文件；检查结果只报告位置和风险类别。

该工具辅助识别常见凭据与运行文件；图片、配置数据流、远端缓存及附件仍需单独核验，不能将扫描通过当作没有任何安全风险的证明。
