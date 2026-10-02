# 依赖与可复现安装

基础依赖范围位于 `server/requirements.txt`，发布时使用 `server/requirements.lock` 固定解析后的版本与分发文件 SHA-256。锁文件包含必要的平台条件，适用于 Python 3.11/3.12；Windows 安装器自动使用该文件。

```powershell
python -m pip install --require-hashes -r server/requirements.lock
```

内置媒体处理另使用 `server/media-requirements.lock`，其中包括基础与媒体依赖。FFmpeg/ffprobe 是独立系统程序，转录模型需要使用者自行安装。直接录制和上传无需这些可选依赖。

```powershell
python -m pip install --require-hashes -r server/media-requirements.lock
```

## 维护锁文件

修改依赖范围后，使用 uv 从公开 PyPI 重新解析；普通安装不重新解析范围。下面的命令在项目根目录执行：

```powershell
uv pip compile server/requirements.txt --universal --python-version 3.11 --generate-hashes --no-header --default-index https://pypi.org/simple --no-config -o server/requirements.lock
uv pip compile server/requirements.txt server/media_requirements.txt --universal --python-version 3.11 --generate-hashes --no-header --default-index https://pypi.org/simple --no-config -o server/media-requirements.lock
```

检查版本变化及对应许可证，在新虚拟环境安装基础依赖并运行离线检查。涉及可选媒体处理时，再验证 FFmpeg、模型与相应处理路径。固定版本与哈希提供安装一致性和完整性校验，仍需定期评审更新和已知漏洞。
