"""Recording preparation: original upload, built-in editing, or a user-owned tool."""
import json
import os
import shutil
import signal
import subprocess
from pathlib import Path


def processing_default():
    mode = os.environ.get('RECORDER_PROCESSING_MODE',
                          'direct' if os.environ.get('RECORDER_MODE') == 'local' else 'builtin')
    if mode not in ('direct', 'builtin', 'external'):
        raise ValueError('RECORDER_PROCESSING_MODE 只能是 direct、builtin 或 external')
    return mode


def processor_settings():
    value = os.environ.get('RECORDER_PROCESSOR_CONFIG', '').strip()
    if not value:
        raise ValueError('请先配置自己的剪辑工具：RECORDER_PROCESSOR_CONFIG')
    config = json.loads(Path(value).read_text(encoding='utf-8-sig'))
    command = config.get('command')
    if (not isinstance(command, list) or not command or
            not all(isinstance(item, str) and item for item in command) or
            sum(item.count('{request}') for item in command) != 1):
        raise ValueError('剪辑工具 command 必须是参数数组，并包含一个 {request}')
    timeout = config.get('timeout_seconds', 3600)
    if not isinstance(timeout, int) or not 1 <= timeout <= 86400:
        raise ValueError('剪辑工具 timeout_seconds 必须在 1 到 86400 之间')
    skill = config.get('skill_path', '')
    if skill and not Path(skill).is_file():
        raise ValueError('剪辑 Skill 文件不存在，请检查 skill_path')
    return command, timeout, skill


def processor_available():
    try:
        processor_settings()
        return True
    except (OSError, ValueError, TypeError):
        return False


def video_type(path):
    with Path(path).open('rb') as stream:
        header = stream.read(16)
    if header[4:8] == b'ftyp':
        return ('.mov', 'video/quicktime') if header[8:12] == b'qt  ' else ('.mp4', 'video/mp4')
    if header[:4] == b'\x1aE\xdf\xa3':
        return '.webm', 'video/webm'
    # Other imported containers remain generic; do not claim they are MP4.
    return '.bin', 'application/octet-stream'


def run_tool(command, directory, timeout):
    # Editors receive media and their own tool settings, never the uploader's OAuth environment.
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(('GOOGLE_CLIENT_', 'OWNER_', 'DAIGUI_TEXT_'))
                   and k not in ('RECORDER_DATA_DIR', 'RECORDER_BASE_URL', 'RECORDER_PROCESSOR_CONFIG')}
    options = {'env': environment, 'cwd': directory, 'shell': False,
               'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
    if os.name == 'nt':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options['start_new_session'] = True
    process = subprocess.Popen(command, **options)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
        raise


def external_process(job, source, output, progress):
    command, timeout, skill = processor_settings()
    if not shutil.which('ffprobe'):
        raise ValueError('自定义剪辑需要 ffprobe 验证成片，请先安装 FFmpeg')
    output = output.resolve()
    request = output / 'request.json'
    result_path = output / 'result.json'
    # A stale successful response must never authorize a failed retry.
    if result_path.exists():
        result_path.unlink()
    extension, _ = video_type(source)
    input_path = output / ('input' + extension)
    shutil.copyfile(source, input_path)
    payload = {'version': 1, 'job_id': job['id'], 'input_path': str(input_path),
               'output_directory': str(output), 'result_path': str(result_path),
               'skill_path': str(Path(skill).resolve()) if skill else '',
               'metadata': {'title': job.get('title', ''), 'description': job.get('description', '')}}
    request.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    progress('external', '正在运行你配置的剪辑工具', 0.1)
    try:
        returncode = run_tool([part.replace('{request}', str(request)) for part in command], output, timeout)
    except subprocess.TimeoutExpired:
        raise ValueError('剪辑工具超时，原片已保留；请检查工具后重试') from None
    except OSError:
        raise ValueError('无法启动剪辑工具，请检查 command 的程序路径') from None
    if returncode != 0:
        raise ValueError('剪辑工具执行失败，原片已保留；请检查工具后重试')
    if not result_path.is_file():
        raise ValueError('剪辑工具尚未返回 result.json，未开始上传')
    result = json.loads(result_path.read_text(encoding='utf-8-sig'))
    final = Path(result.get('final_path', ''))
    final = (final if final.is_absolute() else output / final).resolve()
    if (not final.is_relative_to(output) or final == input_path.resolve()
            or not final.is_file() or not final.stat().st_size):
        raise ValueError('剪辑工具未返回输出目录内的独立成片，未开始上传')
    try:
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(final)],
                               capture_output=True, timeout=60, check=True,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        info = json.loads(probe.stdout)
        if (float(info.get('format', {}).get('duration', 0)) <= 0
                or not any(s.get('codec_type') == 'video' for s in info.get('streams', []))):
            raise ValueError()
    except (subprocess.SubprocessError, ValueError, OSError):
        raise ValueError('剪辑工具返回的成片无法验证，未开始上传') from None
    for key in ('title', 'description', 'transcript'):
        if key in result and not isinstance(result[key], str):
            raise ValueError('剪辑工具返回的文案格式不正确')
    return {**result, 'final_path': str(final), 'metadata_required': False}


def process_recording(job, source, output, progress):
    mode = job.get('processing_mode') or processing_default()
    if mode == 'direct':
        # Upload original bytes, preserving its container and the local original.
        title = job.get('title', '').strip() or Path(job['filename']).stem[:100] or '录制视频'
        title = title.replace('<', '').replace('>', '').replace('\n', ' ').replace('\r', ' ')
        return {'final_path': str(source.resolve()), 'title': title,
                'description': job.get('description', ''), 'metadata_required': False,
                'stats': {'processing_mode': 'direct'}}
    if mode == 'external':
        result = external_process(job, source, output, progress)
        result['title'] = result.get('title', '').strip() or job.get('title') or Path(job['filename']).stem[:100] or '录制视频'
        result.setdefault('description', job.get('description', ''))
        return result
    if mode == 'builtin':
        from media_pipeline import process_job
        return process_job(str(source), str(output), progress,
                           metadata_overrides={'title': job.get('title', ''), 'description': job.get('description', '')})
    raise ValueError('未知的录制处理方式')
