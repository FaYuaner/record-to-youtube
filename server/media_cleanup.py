"""Clean one verified YouTube job's generated media, one explicit file at a time."""
import os
from pathlib import Path
import re
import time
import uuid

import db


def clean_uploaded_media(job):
    if job.get('cleanup_state') == 'complete':
        return
    if (job.get('state') not in ('private', 'unlisted', 'published') or not job.get('video_id')
            or job.get('_processed_video_id') != job['video_id']
            or time.time() - job.get('_processed_at', 0) > 3600):
        raise ValueError('必須先確認 YouTube 已成功處理這支影片，才能清理伺服器副本')
    jid = str(uuid.UUID(job['id']))
    parent = db.DATA_DIR / 'jobs'
    root = parent / jid
    if root.is_symlink() or parent.is_symlink() or root.resolve().parent != parent.resolve():
        raise ValueError('錄影儲存路徑無法確認，尚未清理')
    processed = root / 'processed'
    if processed.is_symlink():
        raise ValueError('成片儲存路徑無法確認，尚未清理')
    candidates = []
    # Only recorder-owned media filenames are eligible. Text, manifests, metadata,
    # unknown files, other jobs and all directories are retained.
    for directory, fixed, pattern in (
        (root, {'original.webm', 'original.assembling'}, r'chunk-\d{6}(?:\.incoming)?'),
        (processed, {'final.mp4', 'original-audio.wav', 'final-audio.wav', 'segments.ffconcat'}, r'segment-\d{5}\.mkv'),
    ):
        if not directory.exists():
            continue
        if not directory.is_dir() or not directory.resolve().is_relative_to(root.resolve()):
            raise ValueError('錄影儲存位置異常，尚未清理')
        for path in directory.iterdir():
            if directory == root and path.name == 'original.webm' and job.get('retain_original'):
                continue
            if path.name not in fixed and not re.fullmatch(pattern, path.name):
                continue
            if path.is_symlink() or not path.is_file() or path.resolve().parent != directory.resolve():
                raise ValueError('錄影檔案位置異常，尚未清理')
            candidates.append(path)
    db.update_job(jid, cleanup_state='running', cleanup_error=None)
    freed = job.get('cleanup_freed_bytes', 0)
    try:
        for path in candidates:
            # No recursive delete, glob deletion, or directory removal. Check the
            # exact file again immediately before this individual unlink.
            if path.is_symlink() or path.resolve().parent not in (root.resolve(), processed.resolve()):
                raise ValueError('錄影檔案位置已改變，清理已暫停')
            size = path.stat().st_size
            os.unlink(path)
            freed += size
            db.update_job(jid, cleanup_freed_bytes=freed)
    except (OSError, ValueError):
        db.update_job(jid, cleanup_state='failed', cleanup_error='伺服器副本尚未完全清理，請重試；YouTube 影片不受影響',
                      _cleanup_attempts=job.get('_cleanup_attempts', 0) + 1, _cleanup_retry_at=time.time() + 300)
        return
    db.update_job(jid, cleanup_state='complete', cleanup_error=None, media_cleaned_at=time.time(),
                  cleanup_freed_bytes=freed, original_ready=bool(job.get('retain_original') and (root / 'original.webm').is_file()), final_ready=False, _final_path=None)
