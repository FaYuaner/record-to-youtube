"""Daigui Recorder: authenticated resumable intake and serial media publishing."""
import asyncio
import logging
import traceback
import hashlib
import math
import os
import posixpath
import re
import secrets
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import auth
import db

PREFIX = urlparse(auth.BASE_URL).path.rstrip('/')
ORIGIN = '{0.scheme}://{0.netloc}'.format(urlparse(auth.BASE_URL))
COOKIE = 'daigui_recorder_session'
CHUNK_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_BYTES = int(os.environ.get('MAX_UPLOAD_BYTES', 1024 * 1024 * 1024))
MIN_FREE_BYTES = int(os.environ.get('MIN_FREE_BYTES', 1024 * 1024 * 1024))
ALLOW_PUBLIC = os.environ.get('ALLOW_PUBLIC_PUBLISH', 'false').lower() == 'true'
STOP = threading.Event()
WAKE = threading.Event()
UPLOAD_LOCK = asyncio.Lock()
BUSY_PUBLISH = threading.Event()
PUBLISH_LOCK = threading.Lock()
MAINTENANCE_LOCK = threading.Lock()
LAST_MAINTENANCE = 0.0
WORKER_LOCK = threading.RLock()
WORKER_THREAD = None
SUPERVISOR_THREAD = None
ACTIVE_JOB = None
WORKER_ERROR = False
LOG = logging.getLogger('recorder.worker')


def worker_enabled():
    return os.environ.get('RECORDER_WORKER_ENABLED', 'false').lower() == 'true'


def worker_alive():
    return bool(WORKER_THREAD and WORKER_THREAD.is_alive())


def recover_interrupted_jobs():
    for job in db.list_jobs():
        if job['state'] == 'processing':
            db.update_job(job['id'], state='queued', message='正在恢復未完成的處理')
        elif job['state'] in ('upload_pausing', 'upload_stopping'):
            apply_upload_command(job['id'])


def ensure_worker():
    global WORKER_THREAD
    with WORKER_LOCK:
        if STOP.is_set() or not worker_enabled():
            return False
        if worker_alive():
            return False
        recover_interrupted_jobs()
        WORKER_THREAD = threading.Thread(target=worker, daemon=True, name='recorder-worker')
        WORKER_THREAD.start()
        LOG.info('Recording worker started')
        return True


def log_worker_error(exc):
    # Stack locations are useful; request URLs, credentials and locals are not logged.
    LOG.error('Worker exception: %s; %s\n%s', type(exc).__name__, safe_failure(exc), ''.join(traceback.format_tb(exc.__traceback__)))


def supervise_worker():
    while not STOP.wait(5):
        try:
            ensure_worker()
        except Exception as exc:
            log_worker_error(exc)


def worker():
    global WORKER_ERROR, ACTIVE_JOB
    while not STOP.is_set():
        try:
            recover_interrupted_jobs()
            worker_loop()
        except Exception as exc:
            WORKER_ERROR = True
            log_worker_error(exc)
            STOP.wait(5)
        finally:
            with WORKER_LOCK:
                ACTIVE_JOB = None

API_RETENTION_SECONDS = 29 * 86400


def maintain_stored_data(force=False, now=None):
    """Local, bounded retention cleanup; publication and disconnect cannot overlap it."""
    global LAST_MAINTENANCE
    current = time.time() if now is None else now
    if not force and current - LAST_MAINTENANCE < 3600:
        return False
    if not MAINTENANCE_LOCK.acquire(blocking=False):
        return False
    try:
        if not PUBLISH_LOCK.acquire(blocking=False):
            return False
        try:
            db.delete_expired_sessions(current)
            auth.maintain_authorization(current)
            db.expire_stale_identities(current - API_RETENTION_SECONDS)
            db.expire_historical_api_data(current - API_RETENTION_SECONDS)
            LAST_MAINTENANCE = current
            return True
        finally:
            PUBLISH_LOCK.release()
    finally:
        MAINTENANCE_LOCK.release()


def job_dir(job_id):
    return db.DATA_DIR / 'jobs' / job_id


def public_job(job):
    fields = ('id', 'state', 'progress', 'message', 'error', 'filename', 'title', 'description', 'privacy', 'auto_publish',
              'received_bytes', 'total_bytes', 'next_chunk', 'created_at', 'updated_at', 'video_id', 'video_url',
              'transcript', 'stats', 'original_ready', 'final_ready', 'metadata_required', 'duplicate_of', 'actual_privacy', 'publication_locked')
    result = {key: job.get(key) for key in fields}
    result['worker_recovering'] = worker_enabled() and (not worker_alive() or WORKER_ERROR)
    result['youtube_checked_at'] = job.get('_api_fetched_at')
    result['server_upload_progress'] = min(1, job.get('received_bytes', 0) / max(1, job.get('total_bytes', 1)))
    result['youtube_upload_progress'] = 1 if job.get('video_id') else min(1, job.get('_upload_bytes', 0) / max(1, job.get('_upload_total', 1)))
    for key in ('cleanup_state', 'cleanup_error', 'media_cleaned_at', 'cleanup_freed_bytes'):
        result[key] = job.get(key)
    return result


def require_session(request, authenticated=True):
    sid = auth.read_sid(request.cookies.get(COOKIE))
    session = db.get_session(sid) if sid else None
    if not session:
        raise HTTPException(401, '請重新開啟頁面並連接 YouTube')
    user = session.get('user')
    if authenticated and (not user or user.get('email') != auth.OWNER_EMAIL or user.get('channel_id') != auth.OWNER_CHANNEL):
        raise HTTPException(401, '請先連接指定的 YouTube 頻道')
    return sid, session


def own_job(request, job_id):
    require_session(request)
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, '找不到這段錄影')
    if job.get('owner') != auth.OWNER_EMAIL:
        raise HTTPException(403, '沒有存取此錄影的權限')
    return job


def check_privacy(privacy):
    if privacy == 'public' and not ALLOW_PUBLIC:
        raise HTTPException(409, '目前尚未啟用公開發布，請先選擇私享或不公開')


def check_metadata(job):
    if not job.get('title', '').strip():
        raise HTTPException(422, '請填寫影片標題')
    validate_metadata_fields(job.get('title', ''), job.get('description', ''))


def validate_metadata_fields(title, description):
    if any(c in title + description for c in '<>') or '\n' in title or '\r' in title:
        raise HTTPException(422, '標題不可換行；標題和簡介不可包含 < 或 >')
    if len(title) > 100 or len(description.encode('utf-8')) > 5000:
        raise HTTPException(422, '標題最多 100 個字元，簡介最多 5,000 位元組（中文約 1,600 字）')


def safe_failure(exc):
    if isinstance(exc, (httpx.HTTPError, ConnectionError, TimeoutError)):
        return '外部服務暫時無法連線，請稍後重試；已保存的影片不受影響'
    text = str(exc)
    if 'https://' in text or 'http://' in text or 'Bearer ' in text or 'token' in text.lower():
        return '外部服務回應異常，請稍後重試或重新連接頻道'
    return text[:300] or '處理失敗，請稍後重試'


def progress_update(job_id, stage, message, progress):
    db.update_job(job_id, message=str(message)[:200], progress=max(0, min(1, float(progress))), _stage=stage)


def process_media(job):
    from media_pipeline import process_job
    db.update_job(job['id'], state='processing', error=None, message='正在處理錄影', progress=0)
    output = job_dir(job['id']) / 'processed'
    output.mkdir(exist_ok=True)
    result = process_job(str(job_dir(job['id']) / 'original.webm'), str(output),
                         lambda stage, message, progress: progress_update(job['id'], stage, message, progress),
                         metadata_overrides={'title': job.get('title', ''), 'description': job.get('description', '')})
    final = Path(result['final_path']).resolve()
    if not final.is_relative_to(output.resolve()) or not final.is_file():
        raise ValueError('剪輯服務未產生有效成片')
    pending = bool(result.get('metadata_required'))
    with db.LOCK:
        current = db.get_job(job['id'])
        current.update(state='ready', progress=1, final_ready=True, _final_path=str(final),
                       transcript=result.get('transcript', ''), stats=result.get('stats', {}),
                       title=current.get('title') if current.get('title', '').strip() else result.get('title', ''),
                       description=current.get('description') if current.get('description', '').strip() else result.get('description', ''),
                       metadata_required=pending,
                       message='成片已完成，請填寫標題與說明後發布' if pending else '成片與文案已完成',
                       error=result.get('error') if pending else None)
        if current.get('auto_publish') and not pending and current.get('title') and (current['privacy'] != 'public' or ALLOW_PUBLIC):
            check_metadata(current)
            current.update(state='publish_queued', message='已排入 YouTube 上傳佇列', progress=0)
        db.save_job(current)


def google_headers():
    return {'Authorization': 'Bearer ' + auth.access_token()}


def accept_video_response(job_id, response):
    try:
        video_id = response.json()['id']
    except (ValueError, KeyError):
        raise ValueError('YouTube 回應未包含影片編號，請重試以核查原上傳狀態')
    if not re.fullmatch(r'[A-Za-z0-9_-]{6,32}', video_id):
        raise ValueError('YouTube 影片編號格式異常')
    db.update_job(job_id, video_id=video_id, video_url='https://www.youtube.com/watch?v=' + video_id,
                  state='youtube_processing', message='上傳完成，YouTube 正在處理影片', progress=1, _next_poll=0, error=None,
                  _api_fetched_at=time.time(), _upload_command=None)


def acknowledged_offset(response):
    value = response.headers.get('Range', '')
    match = re.fullmatch(r'bytes=0-(\d+)', value)
    return int(match.group(1)) + 1 if match else 0


def apply_upload_command(job_id):
    """Acknowledge a pause/stop only after the current YouTube request has returned."""
    with db.LOCK:
        current = db.get_job(job_id)
        command = current.get('_upload_command') if current else None
        if command not in ('pause', 'stop') or current.get('video_id'):
            return False
        paused = command == 'pause'
        db.update_job(job_id, state='youtube_paused' if paused else 'youtube_stopped',
                      message='YouTube 上傳已暫停，可繼續上傳' if paused else 'YouTube 上傳已中斷；成片仍保存在伺服器，重新上傳前會核查原上傳進度',
                      _upload_command=None, error=None)
        return True


def upload_video(job):
    if apply_upload_command(job['id']):
        return
    if job.get('publication_locked'):
        raise ValueError('此影片已清除 Google 上傳記錄；為避免重複發布，請在 YouTube 工作室核查')
    if job.get('video_id'):
        db.update_job(job['id'], state='youtube_processing', _next_poll=0)
        return
    if job['privacy'] == 'public' and not ALLOW_PUBLIC:
        raise ValueError('目前尚未啟用公開發布，請改選私享或不公開')
    if not job.get('title', '').strip():
        raise ValueError('請先填寫影片標題')
    path = Path(job['_final_path'])
    total = path.stat().st_size
    db.update_job(job['id'], state='publishing', message='正在上傳到 YouTube', error=None)
    if apply_upload_command(job['id']):
        return
    headers = google_headers()
    with auth.google_client() as client:
        if job.get('_upload_url'):
            if apply_upload_command(job['id']):
                return
            url = auth.decrypt(job['_upload_url'])
            probe = client.put(url, headers={**headers, 'Content-Length': '0', 'Content-Range': f'bytes */{total}'}, content=b'')
            if probe.status_code in (200, 201):
                accept_video_response(job['id'], probe)
                return
            if probe.status_code in (404, 410):
                raise ValueError('原上傳工作已失效且結果無法確認；為避免重複發布，請先人工核查 YouTube 工作室')
            if probe.status_code != 308:
                raise ValueError(f'YouTube 上傳狀態核查失敗（{probe.status_code}），請重試')
            offset = acknowledged_offset(probe)
            db.update_job(job['id'], _api_fetched_at=time.time())
        else:
            if apply_upload_command(job['id']):
                return
            metadata = {'snippet': {'title': job['title'], 'description': job.get('description', ''), 'categoryId': '22',
                                    'defaultLanguage': 'zh-Hant', 'defaultAudioLanguage': 'zh'},
                        'status': {'privacyStatus': job['privacy'], 'selfDeclaredMadeForKids': False}}
            res = client.post('https://www.googleapis.com/upload/youtube/v3/videos', params={'uploadType': 'resumable', 'part': 'snippet,status'},
                              headers={**headers, 'X-Upload-Content-Type': 'video/mp4', 'X-Upload-Content-Length': str(total)}, json=metadata)
            if res.status_code not in (200, 201):
                raise ValueError(f'YouTube 無法建立上傳工作（{res.status_code}），請核查 API 設定及配額')
            url = res.headers.get('Location', '')
            parsed = urlparse(url)
            if parsed.scheme != 'https' or parsed.hostname not in ('www.googleapis.com', 'youtube.googleapis.com'):
                raise ValueError('YouTube 未回傳有效的安全上傳地址')
            offset = 0
            # Persist before any content transfer: retries resume this same YouTube upload.
            db.update_job(job['id'], _upload_url=auth.encrypt(url), _upload_bytes=0, _upload_total=total, _api_fetched_at=time.time())
        if apply_upload_command(job['id']):
            return
        if offset > total:
            raise ValueError('YouTube 上傳進度異常')
        with path.open('rb') as stream:
            stream.seek(offset)
            while offset < total:
                if STOP.is_set():
                    return
                if apply_upload_command(job['id']):
                    return
                content = stream.read(CHUNK_BYTES)
                end = offset + len(content) - 1
                res = client.put(url, headers={**google_headers(), 'Content-Type': 'video/mp4', 'Content-Length': str(len(content)),
                                               'Content-Range': f'bytes {offset}-{end}/{total}'}, content=content)
                if res.status_code in (200, 201):
                    accept_video_response(job['id'], res)
                    return
                if res.status_code != 308:
                    raise ValueError(f'YouTube 上傳中斷（{res.status_code}）；重試會先核查原上傳進度')
                new_offset = acknowledged_offset(res)
                if new_offset <= offset or new_offset > total:
                    raise ValueError('YouTube 未確認新的上傳進度，請重試')
                offset = new_offset
                stream.seek(offset)
                db.update_job(job['id'], _upload_bytes=offset, progress=offset / total, message='正在上傳到 YouTube', _api_fetched_at=time.time())
                if apply_upload_command(job['id']):
                    return
        raise ValueError('YouTube 尚未確認影片完成，請重試核查原上傳狀態')


def poll_video(job):
    with auth.google_client() as client:
        response = client.get('https://www.googleapis.com/youtube/v3/videos', params={'part': 'status,processingDetails', 'id': job['video_id']}, headers=google_headers())
    if response.status_code != 200:
        raise ValueError(f'YouTube 處理狀態暫時無法查詢（{response.status_code}），重試不會重新上傳')
    items = response.json().get('items', [])
    if not items:
        raise ValueError('暫時查不到已上傳的影片，請在 YouTube 工作室核查')
    status = items[0].get('status', {})
    db.update_job(job['id'], _api_fetched_at=time.time())
    processing = items[0].get('processingDetails', {}).get('processingStatus')
    if processing in ('failed', 'terminated') or status.get('uploadStatus') in ('failed', 'rejected', 'deleted'):
        raise ValueError('YouTube 無法處理這段影片，請開啟工作室查看原因')
    if processing == 'succeeded' or (not processing and status.get('uploadStatus') == 'processed'):
        privacy = status.get('privacyStatus', 'private')
        state = 'published' if privacy == 'public' else privacy
        messages = {'public': '已公開發布到 YouTube', 'private': '影片已上傳，YouTube 目前設為私享', 'unlisted': '影片已上傳，設為不公開'}
        message = messages.get(privacy, '影片已處理')
        if privacy != job['privacy']:
            message += '。YouTube 實際可見範圍與所選設定不同，請到 YouTube 工作室確認；本工具未將影片改為私人。'
        current = db.update_job(job['id'], state=state, actual_privacy=privacy, progress=1,
                                message=message, error=None,
                                _processed_video_id=job['video_id'], _processed_at=time.time())
        if current.get('cleanup_after_upload', True) and current.get('cleanup_state') != 'complete':
            from media_cleanup import clean_uploaded_media
            clean_uploaded_media(current)
    else:
        db.update_job(job['id'], state='youtube_processing', message='YouTube 正在處理影片', _next_poll=time.time() + 30)


def worker_loop():
    global ACTIVE_JOB, WORKER_ERROR
    while not STOP.is_set():
        maintain_stored_data()
        jobs = list(reversed(db.list_jobs()))
        # Deduplicated uploads may have their own intake files. Reuse the same
        # remote video and verify it before cleaning that duplicate's directory.
        by_id = {j['id']: j for j in jobs}
        for duplicate in jobs:
            original = by_id.get(duplicate.get('duplicate_of'))
            if (original and original.get('video_id') and not duplicate.get('video_id')
                    and duplicate['state'] in ('ready', 'duplicate_waiting')
                    and not duplicate.get('publication_locked')):
                db.update_job(duplicate['id'], video_id=original['video_id'], video_url=original.get('video_url'),
                              state='youtube_processing', _next_poll=0, cleanup_after_upload=True)
        with WORKER_LOCK:
            job = next((j for j in reversed(db.list_jobs()) if j['state'] in ('queued', 'publish_queued', 'publishing') or
                        (j['state'] == 'youtube_processing' and j.get('_next_poll', 0) <= time.time()) or
                        (j['state'] in ('private', 'unlisted', 'published') and j.get('video_id')
                         and j.get('cleanup_after_upload', True) and j.get('cleanup_state') != 'complete'
                         and j.get('_cleanup_attempts', 0) < 3 and j.get('_cleanup_retry_at', 0) <= time.time())), None)
            ACTIVE_JOB = job['id'] if job else None
            WORKER_ERROR = False
        if not job:
            WAKE.wait(5)
            WAKE.clear()
            continue
        resume = 'queued' if job['state'] == 'queued' else ('youtube_processing' if job.get('video_id') else 'publish_queued')
        try:
            if job['state'] == 'queued':
                process_media(job)
            else:
                with PUBLISH_LOCK:
                    BUSY_PUBLISH.set()
                    if job.get('video_id'):
                        poll_video(job)
                    else:
                        upload_video(job)
        except Exception as exc:
            log_worker_error(exc)
            if apply_upload_command(job['id']):
                continue
            current = db.get_job(job['id'])
            if current.get('state') in ('private', 'unlisted', 'published'):
                db.update_job(job['id'], cleanup_state='failed', cleanup_error=safe_failure(exc),
                              _cleanup_attempts=current.get('_cleanup_attempts', 0) + 1,
                              _cleanup_retry_at=time.time() + 300)
            elif not current.get('publication_locked'):
                db.update_job(job['id'], state='failed', error=safe_failure(exc), message='處理暫停，請查看原因', _resume_state=resume)
        finally:
            BUSY_PUBLISH.clear()
            with WORKER_LOCK:
                ACTIVE_JOB = None


@asynccontextmanager
async def lifespan(app):
    global SUPERVISOR_THREAD
    auth.validate_settings()
    db.init()
    auth.init()
    (db.DATA_DIR / 'jobs').mkdir(exist_ok=True)
    STOP.clear()
    ensure_worker()
    if worker_enabled():
        SUPERVISOR_THREAD = threading.Thread(target=supervise_worker, daemon=True, name='recorder-supervisor')
        SUPERVISOR_THREAD.start()
    yield
    STOP.set()
    WAKE.set()
    for thread in (WORKER_THREAD, SUPERVISOR_THREAD):
        if thread and thread.is_alive():
            thread.join(timeout=3)


app = FastAPI(title='daigui Recorder', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
api = APIRouter(prefix=PREFIX + '/api')


@app.middleware('http')
async def protection(request, call_next):
    if request.url.path == PREFIX:
        return RedirectResponse(PREFIX + '/', status_code=307)
    private_page = posixpath.normpath(request.url.path) in (PREFIX, PREFIX + '/index.html')
    if private_page:
        try:
            require_session(request)
        except HTTPException:
            response = FileResponse(Path(__file__).parent / 'static' / 'login.html')
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['Referrer-Policy'] = 'same-origin'
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
            return response
    if request.url.path.startswith(PREFIX + '/api/') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if request.headers.get('origin') != ORIGIN:
            return JSONResponse({'detail': '請從工具本身的頁面操作'}, status_code=403)
        try:
            _, session = require_session(request, authenticated=False)
        except HTTPException as exc:
            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
        if not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session.get('csrf_token', '')):
            return JSONResponse({'detail': '頁面驗證已失效，請重新整理後再試'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Permissions-Policy'] = 'camera=(self), microphone=(self), geolocation=()'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if private_page or request.url.path.startswith(PREFIX + '/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@api.get('/health')
def health():
    healthy = not worker_enabled() or (worker_alive() and not WORKER_ERROR and bool(SUPERVISOR_THREAD and SUPERVISOR_THREAD.is_alive()))
    return JSONResponse({'ok': healthy, 'worker_alive': worker_alive(), 'recovering': WORKER_ERROR}, status_code=200 if healthy else 503)


@api.get('/session')
def session_info(request: Request):
    maintain_stored_data()
    sid = auth.read_sid(request.cookies.get(COOKIE))
    session = db.get_session(sid) if sid else None
    if not session:
        sid = secrets.token_urlsafe(32)
        session = {'csrf_token': secrets.token_urlsafe(32)}
        db.save_session(sid, session)
    elif session.get('user'):
        # Identity and the remembered browser are separate from Google access-token renewal.
        if time.time() - session.get('identity_fetched_at', 0) >= 86400:
            try:
                auth.refresh_identity(session)
                session.pop('auth_error', None)
            except Exception as exc:
                if not db.get_session(sid):
                    session = {'csrf_token': secrets.token_urlsafe(32)}
                    sid = secrets.token_urlsafe(32)
                session['auth_error'] = safe_failure(exc)
        db.save_session(sid, session)
    pending_bytes = sum((max(0, j['total_bytes'] - j.get('received_bytes', 0)) * 4 if j['state'] == 'receiving'
                        else j['total_bytes'] * 2) for j in db.list_jobs() if j['state'] in ('receiving', 'queued', 'processing'))
    available_upload_bytes = max(0, min(MAX_UPLOAD_BYTES, (shutil.disk_usage(db.DATA_DIR).free - MIN_FREE_BYTES - pending_bytes) // 4))
    response = JSONResponse({'authenticated': bool(session.get('user')), 'csrf_token': session['csrf_token'],
                             'user': session.get('user'), 'oauth_configured': auth.configured(),
                             'auth_error': session.get('auth_error') or db.secret_get('authorization_notice'),
                             'limits': {'chunk_bytes': CHUNK_BYTES, 'max_upload_bytes': MAX_UPLOAD_BYTES, 'available_upload_bytes': available_upload_bytes},
                             'default_privacy': 'private', 'default_auto_publish': False, 'public_enabled': ALLOW_PUBLIC})
    response.set_cookie(COOKIE, auth.sign_sid(sid), max_age=auth.SESSION_SECONDS if session.get('user') else 43200,
                        httponly=True, secure=True, samesite='lax', path=PREFIX + '/')
    return response


class RecorderPreferences(BaseModel):
    revision: int = Field(ge=0)
    privacy: Literal['public', 'private', 'unlisted']
    auto_publish: bool
    quality: Literal['1080', '720', '480']
    orientation: Literal['auto', 'portrait', 'landscape']


@api.get('/preferences')
def recorder_preferences(request: Request):
    require_session(request)
    import json
    with db.LOCK:
        saved = db.secret_get('recorder_preferences')
    return json.loads(saved) if saved else {'revision': 0}


@api.put('/preferences')
def save_recorder_preferences(request: Request, payload: RecorderPreferences):
    require_session(request)
    check_privacy(payload.privacy)
    import json
    with db.LOCK:
        saved = db.secret_get('recorder_preferences')
        current = json.loads(saved) if saved else {'revision': 0}
        if payload.revision != current['revision']:
            raise HTTPException(409, '另一台裝置已更新設定。請重新讀取帳號設定後再修改，這次輸入尚未覆蓋雲端。')
        result = payload.model_dump()
        result['revision'] += 1
        db.secret_put('recorder_preferences', json.dumps(result))
    return result


@api.post('/oauth/start')
def oauth_start(request: Request):
    sid, session = require_session(request, authenticated=False)
    try:
        url = auth.begin(session)
    except Exception as exc:
        raise HTTPException(503, safe_failure(exc))
    db.save_session(sid, session)
    return {'authorization_url': url}


@api.get('/oauth/callback')
def oauth_callback(request: Request, code: str = '', state: str = '', error: str = ''):
    sid, session = require_session(request, authenticated=False)
    if error or not code:
        session.pop('oauth', None)
        db.save_session(sid, session)
        return RedirectResponse(PREFIX + '/?auth=cancelled', status_code=303)
    try:
        auth.finish(code, state, session)
        session.pop('auth_error', None)
        new_sid = secrets.token_urlsafe(32)
        session['csrf_token'] = secrets.token_urlsafe(32)
        db.save_session(new_sid, session)
        db.delete_session(sid)
        response = RedirectResponse(PREFIX + '/?auth=connected', status_code=303)
        response.set_cookie(COOKIE, auth.sign_sid(new_sid), max_age=auth.SESSION_SECONDS, httponly=True, secure=True, samesite='lax', path=PREFIX + '/')
        return response
    except Exception as exc:
        session.pop('oauth', None)
        session['auth_error'] = safe_failure(exc)
        db.save_session(sid, session)
        return RedirectResponse(PREFIX + '/?auth=failed', status_code=303)


@api.post('/logout')
def logout(request: Request):
    sid, _ = require_session(request, authenticated=False)
    db.delete_session(sid)
    response = JSONResponse({'ok': True, 'message': '已登出；背景工作會繼續'})
    response.delete_cookie(COOKIE, path=PREFIX + '/')
    return response


@api.post('/disconnect')
def disconnect(request: Request):
    require_session(request)
    if not PUBLISH_LOCK.acquire(blocking=False):
        raise HTTPException(409, '正在與 YouTube 傳輸，請稍後再解除連接')
    try:
        auth.disconnect()
    except Exception as exc:
        raise HTTPException(502, safe_failure(exc))
    finally:
        PUBLISH_LOCK.release()
    response = JSONResponse({'ok': True, 'message': '已撤銷 Google 授權，清除令牌、頻道資料和本工具的 YouTube 上傳記錄；你的錄影、轉錄與 YouTube 影片仍保留'})
    response.delete_cookie(COOKIE, path=PREFIX + '/')
    return response


class CreateJob(BaseModel):
    client_id: uuid.UUID
    filename: str = Field(min_length=1, max_length=240)
    total_bytes: int = Field(gt=0)
    sha256: str | None = Field(default=None, pattern=r'^[a-fA-F0-9]{64}$')
    title: str = Field(default='', max_length=100)
    description: str = Field(default='', max_length=5000)
    privacy: Literal['public', 'private', 'unlisted'] = 'private'
    auto_publish: bool = False
    retain_original: bool = False


class Metadata(BaseModel):
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    privacy: Literal['public', 'private', 'unlisted'] | None = None
    auto_publish: bool | None = None


class UploadCommand(BaseModel):
    action: Literal['pause', 'stop']


@api.get('/jobs')
def jobs(request: Request):
    require_session(request)
    return {'jobs': [public_job(j) for j in db.list_jobs() if j.get('owner') == auth.OWNER_EMAIL and not j.get('duplicate_of')]}


@api.post('/jobs')
def create_job(request: Request, payload: CreateJob):
    require_session(request)
    check_privacy(payload.privacy)
    validate_metadata_fields(payload.title, payload.description)
    if payload.total_bytes > MAX_UPLOAD_BYTES:
        raise HTTPException(413, '影片超過本次允許的大小，請縮短錄影後再試')
    with db.LOCK:
        old = db.find_job('client_id', str(payload.client_id))
        if old:
            if old['total_bytes'] != payload.total_bytes:
                raise HTTPException(409, '此上傳編號已用於另一段錄影')
            return public_job(old)
        if payload.sha256:
            old = db.find_job('sha256', payload.sha256.lower())
            if old:
                return public_job(old)
        pending = sum((max(0, j['total_bytes'] - j.get('received_bytes', 0)) * 4 if j['state'] == 'receiving'
                       else j['total_bytes'] * 2) for j in db.list_jobs() if j['state'] in ('receiving', 'queued', 'processing'))
        if shutil.disk_usage(db.DATA_DIR).free < payload.total_bytes * 4 + pending + MIN_FREE_BYTES:
            raise HTTPException(507, '伺服器空間不足；錄影仍保存在你的電腦，請聯絡管理者釋放空間')
        jid = str(uuid.uuid4())
        job_dir(jid).mkdir(mode=0o700)
        job = dict(id=jid, client_id=str(payload.client_id), owner=auth.OWNER_EMAIL, filename=Path(payload.filename.replace('\\', '/')).name,
                   total_bytes=payload.total_bytes, sha256=payload.sha256.lower() if payload.sha256 else None,
                   title=payload.title, description=payload.description, privacy=payload.privacy, auto_publish=payload.auto_publish,
                   cleanup_after_upload=True, retain_original=payload.retain_original,
                   state='receiving', progress=0, message='等待錄影上傳', error=None, received_bytes=0, next_chunk=0,
                   created_at=time.time(), original_ready=False, final_ready=False, transcript='', stats={}, metadata_required=False)
        db.save_job(job)
    return public_job(job)


@api.get('/jobs/{job_id}')
def get_job(request: Request, job_id: str):
    return public_job(own_job(request, job_id))


@api.put('/jobs/{job_id}/chunks/{index}')
async def put_chunk(request: Request, job_id: str, index: int):
    async with UPLOAD_LOCK:
        job = own_job(request, job_id)
        if index < 0 or index >= math.ceil(job['total_bytes'] / CHUNK_BYTES):
            raise HTTPException(400, '分片編號無效')
        if job['state'] != 'receiving' or index > job['next_chunk']:
            raise HTTPException(409, '上傳順序已改變，請重新讀取進度後重試')
        expected = min(CHUNK_BYTES, job['total_bytes'] - index * CHUNK_BYTES)
        if shutil.disk_usage(db.DATA_DIR).free < expected + MIN_FREE_BYTES:
            raise HTTPException(507, '伺服器空間不足，請保留本機錄影並稍後重試')
        target = job_dir(job_id) / f'chunk-{index:06d}'
        temporary = job_dir(job_id) / f'chunk-{index:06d}.incoming'
        received = 0
        digest = hashlib.sha256()
        with (nullcontext(None) if target.exists() else temporary.open('wb')) as stream:
            async for block in request.stream():
                received += len(block)
                if received > expected:
                    raise HTTPException(413, '分片大小超出允許範圍')
                digest.update(block)
                if stream is not None:
                    stream.write(block)
            if stream is not None:
                stream.flush()
                os.fsync(stream.fileno())
        if received != expected:
            raise HTTPException(400, '分片資料不完整，請重試')
        if target.exists():
            if hashlib.sha256(target.read_bytes()).digest() != digest.digest():
                raise HTTPException(409, '同一分片內容不一致，請重新開始這段上傳')
        else:
            temporary.replace(target)
        if index == job['next_chunk']:
            job = db.update_job(job_id, received_bytes=job['received_bytes'] + received, next_chunk=index + 1,
                                progress=(job['received_bytes'] + received) / job['total_bytes'], message='正在接收錄影')
        return {'received_bytes': job['received_bytes'], 'next_chunk': job['next_chunk']}


@api.post('/jobs/{job_id}/complete')
async def complete(request: Request, job_id: str):
    async with UPLOAD_LOCK:
        job = own_job(request, job_id)
        if job.get('duplicate_of'):
            return public_job(db.get_job(job['duplicate_of']))
        if job['state'] != 'receiving':
            return public_job(job)
        if job['received_bytes'] != job['total_bytes']:
            raise HTTPException(409, '錄影尚未完整上傳，請先完成剩餘分片')
        def assemble():
            temp = job_dir(job_id) / 'original.assembling'
            sha = hashlib.sha256()
            with temp.open('wb') as dest:
                for index in range(job['next_chunk']):
                    with (job_dir(job_id) / f'chunk-{index:06d}').open('rb') as source:
                        while block := source.read(1024 * 1024):
                            sha.update(block)
                            dest.write(block)
                dest.flush()
                os.fsync(dest.fileno())
            if temp.stat().st_size != job['total_bytes']:
                raise ValueError('合併後的錄影大小不符，請重試')
            actual_hash = sha.hexdigest()
            if job.get('sha256') and actual_hash != job['sha256']:
                raise ValueError('錄影完整性驗證失敗，請重新上傳原始檔案')
            temp.replace(job_dir(job_id) / 'original.webm')
            return actual_hash
        try:
            actual_hash = await asyncio.to_thread(assemble)
        except Exception as exc:
            raise HTTPException(422, safe_failure(exc))
        with db.LOCK:
            old = db.find_job('sha256', actual_hash, exclude=job_id)
            if old:
                db.update_job(job_id, duplicate_of=old['id'], original_ready=True, state='ready', message='這段錄影已存在，使用既有工作')
                return public_job(old)
            job = db.update_job(job_id, sha256=actual_hash, original_ready=True, state='queued', progress=0, message='已保存錄影，等待剪輯')
        WAKE.set()
        return public_job(job)


@api.get('/jobs/{job_id}/media')
def media(request: Request, job_id: str, kind: Literal['original', 'final'] = 'final', download: bool = False):
    job = own_job(request, job_id)
    path = job_dir(job_id) / 'original.webm' if kind == 'original' else Path(job.get('_final_path') or job_dir(job_id) / '__missing__')
    if not path.resolve().is_relative_to(job_dir(job_id).resolve()) or not path.is_file():
        raise HTTPException(404, '伺服器影片已清理，請在 YouTube 觀看或使用電腦上的原片' if job.get('cleanup_state') == 'complete' else '影片尚未準備好')
    extension, media_type = '.mp4', 'video/mp4'
    if kind == 'original':
        with path.open('rb') as source:
            header = source.read(16)
        if header[4:8] == b'ftyp':
            if header[8:12] == b'qt  ':
                extension, media_type = '.mov', 'video/quicktime'
        else:
            extension, media_type = '.webm', 'video/webm'
    return FileResponse(path, media_type=media_type,
                        filename=f'daigui-{job_id[:8]}-{kind}' + extension,
                        content_disposition_type='attachment' if download else 'inline')


@api.patch('/jobs/{job_id}')
def metadata(request: Request, job_id: str, payload: Metadata):
    job = own_job(request, job_id)
    if job.get('publication_locked'):
        raise HTTPException(409, '此影片已清除 Google 上傳記錄，請在 YouTube 工作室修改原影片')
    if job.get('video_id') or job.get('_upload_url') or job['state'] in ('processing', 'publishing', 'publish_queued'):
        raise HTTPException(409, '目前工作已開始處理或上傳，暫時無法修改文案；已上傳影片請至 YouTube 工作室編輯')
    fields = payload.model_dump(exclude_none=True)
    if 'privacy' in fields:
        check_privacy(fields['privacy'])
    validate_metadata_fields(fields.get('title', job.get('title', '')), fields.get('description', job.get('description', '')))
    combined = {**job, **fields}
    if combined.get('title', '').strip() and combined.get('description', '').strip():
        fields['metadata_required'] = False
        fields['error'] = None
        if job.get('final_ready'):
            fields['message'] = '文案已儲存，可以發布'
            if combined.get('auto_publish'):
                check_privacy(combined['privacy'])
                fields.update(state='publish_queued', progress=0, message='文案已儲存，等待上傳 YouTube')
    elif job.get('final_ready'):
        fields.update(metadata_required=True, state='queued', message='正在依口播內容補齊空白文案', progress=0)
    updated = db.update_job(job_id, **fields)
    WAKE.set()
    return public_job(updated)


@api.post('/jobs/{job_id}/publish')
def publish(request: Request, job_id: str):
    job = own_job(request, job_id)
    if job.get('publication_locked'):
        raise HTTPException(409, '此影片已清除 Google 上傳記錄；為避免重複發布，請在 YouTube 工作室核查')
    if job.get('video_id') or job['state'] in ('publish_queued', 'publishing', 'youtube_processing'):
        return public_job(job)
    if job['state'] not in ('ready', 'failed'):
        raise HTTPException(409, '影片仍在處理，請完成後再發布')
    if not job.get('final_ready'):
        raise HTTPException(409, '請等成片製作完成後再發布')
    check_privacy(job['privacy'])
    check_metadata(job)
    job = db.update_job(job_id, state='publish_queued', message='已排入 YouTube 上傳佇列', error=None, progress=0)
    WAKE.set()
    return public_job(job)


@api.post('/jobs/{job_id}/upload-control')
def upload_control(request: Request, job_id: str, payload: UploadCommand):
    with WORKER_LOCK, db.LOCK:
        job = own_job(request, job_id)
        if job.get('video_id') or job['state'] in ('youtube_processing', 'private', 'unlisted', 'published'):
            raise HTTPException(409, 'YouTube 上傳已完成，請查看影片狀態')
        if job['state'] in ('youtube_paused', 'youtube_stopped'):
            return public_job(job)
        if job['state'] not in ('publish_queued', 'publishing', 'upload_pausing', 'upload_stopping'):
            raise HTTPException(409, '這段影片目前沒有進行中的 YouTube 上傳')
        active = ACTIVE_JOB == job_id and worker_alive()
        if active:
            state = 'upload_pausing' if payload.action == 'pause' else 'upload_stopping'
            message = '正在等待目前傳輸片段結束，然後暫停上傳' if payload.action == 'pause' else '正在等待目前傳輸片段結束，然後中斷上傳'
        else:
            state = 'youtube_paused' if payload.action == 'pause' else 'youtube_stopped'
            message = 'YouTube 上傳已暫停，可繼續上傳' if payload.action == 'pause' else 'YouTube 上傳已中斷；成片仍保存在伺服器，重新上傳前會核查原上傳進度'
        return public_job(db.update_job(job_id, state=state, message=message, _upload_command=payload.action))


@api.post('/jobs/{job_id}/retry')
def retry(request: Request, job_id: str):
    with WORKER_LOCK:
        job = own_job(request, job_id)
        if job.get('publication_locked'):
            raise HTTPException(409, '此影片已清除 Google 上傳記錄，請在 YouTube 工作室核查；不會再次上傳')
        if not worker_enabled():
            raise HTTPException(503, '背景處理目前已停用，請聯絡管理員')
        if ACTIVE_JOB == job_id and worker_alive():
            return {**public_job(job), 'retry_message': '這段錄製正在處理，無須重複啟動。'}
        state = job['state']
        if state == 'receiving':
            raise HTTPException(409, '原片尚未上傳完成，請先繼續上傳原片。')
        if state in ('private', 'unlisted', 'published') and job.get('cleanup_state') != 'failed':
            return {**public_job(job), 'retry_message': '影片已上傳，無須重複處理。'}
        if state == 'ready' and not job.get('metadata_required'):
            return {**public_job(job), 'retry_message': '成片已完成，請使用上傳 YouTube 按鈕。'}
        if job.get('cleanup_state') == 'failed' and job.get('video_id'):
            job = db.update_job(job_id, cleanup_state='pending', cleanup_error=None, _cleanup_attempts=0, _cleanup_retry_at=0)
        if job.get('video_id'):
            target = 'youtube_processing'
        elif job.get('_upload_url'):
            target = 'publishing'
        elif job.get('metadata_required'):
            target = 'queued'
        elif state == 'failed':
            target = job.get('_resume_state', 'queued')
        elif state in ('youtube_paused', 'youtube_stopped'):
            target = 'publish_queued'
        else:
            target = state
        if target == 'processing':
            if worker_alive():
                raise HTTPException(409, '正在確認處理狀態，請稍後重試。')
            target = 'queued'
        if target == 'queued' and not (job_dir(job_id) / 'original.webm').is_file():
            raise HTTPException(409, '伺服器原片缺失，請保留並重新上傳電腦上的原片。')
        if target in ('publishing', 'publish_queued'):
            check_privacy(job['privacy'])
            check_metadata(job)
        if target not in ('queued', 'publish_queued', 'publishing', 'youtube_processing'):
            raise HTTPException(409, '目前狀態無法重試，請重新整理查看最新進度。')
        if state != target or state == 'failed' or job.get('metadata_required') or job.get('_upload_command'):
            job = db.update_job(job_id, state=target, error=None, message='已安排重試', _next_poll=0, _upload_command=None)
        restarted = ensure_worker()
        WAKE.set()
        message = '背景已恢復，任務等待開始。' if restarted else ('正在處理其他錄製，這段已排隊。' if ACTIVE_JOB and ACTIVE_JOB != job_id else '已通知背景處理，正在等待開始。')
        return {**public_job(db.get_job(job_id)), 'retry_message': message}


app.include_router(api)
STATIC_DIR = Path(__file__).parent / 'static'
if STATIC_DIR.is_dir():
    app.mount(PREFIX or '/', StaticFiles(directory=STATIC_DIR, html=True), name='static')
