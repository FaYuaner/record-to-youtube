"""Small SQLite store. All mutable data lives outside the static web root."""
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get('RECORDER_DATA_DIR', './data')).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
try:
    DATA_DIR.chmod(0o700)
except OSError:
    pass
SESSION_SECONDS = 365 * 86400
LOCK = threading.RLock()
DB_PATH = DATA_DIR / 'recorder.sqlite3'


@contextmanager
def connection():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=30000')
    try:
        with con:
            yield con
    finally:
        con.close()


def init():
    with LOCK, connection() as con:
        con.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, client_id TEXT UNIQUE NOT NULL, sha256 TEXT, created_at REAL NOT NULL, data TEXT NOT NULL)')
        con.execute('CREATE INDEX IF NOT EXISTS jobs_sha ON jobs(sha256)')
        con.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, expires_at REAL NOT NULL, data TEXT NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
    try:
        DB_PATH.chmod(0o600)
    except OSError:
        pass


def get_job(job_id):
    with connection() as con:
        row = con.execute('SELECT data FROM jobs WHERE id=?', (job_id,)).fetchone()
    return json.loads(row['data']) if row else None


def find_job(field, value, exclude=None):
    if field not in ('client_id', 'sha256'):
        raise ValueError('invalid field')
    with connection() as con:
        rows = con.execute(f'SELECT id,data FROM jobs WHERE {field}=? ORDER BY created_at', (value,)).fetchall()
    return next((json.loads(r['data']) for r in rows if r['id'] != exclude), None)


def list_jobs():
    with connection() as con:
        return [json.loads(r['data']) for r in con.execute('SELECT data FROM jobs ORDER BY created_at DESC')]


def save_job(job):
    job['updated_at'] = time.time()
    with LOCK, connection() as con:
        con.execute('INSERT INTO jobs(id,client_id,sha256,created_at,data) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET sha256=excluded.sha256,data=excluded.data',
                    (job['id'], job['client_id'], job.get('sha256'), job['created_at'], json.dumps(job, ensure_ascii=False)))
    return job


def update_job(job_id, **fields):
    with LOCK:
        job = get_job(job_id)
        if job is None:
            return None
        job.update(fields)
        return save_job(job)


def get_session(sid):
    with connection() as con:
        row = con.execute('SELECT data FROM sessions WHERE id=? AND expires_at>?', (sid, time.time())).fetchone()
    return json.loads(row['data']) if row else None


def save_session(sid, data):
    now = time.time()
    expires_at = now + SESSION_SECONDS if data.get('user') else now + 43200
    if data.get('user'):
        data.setdefault('identity_fetched_at', now)
    with LOCK, connection() as con:
        con.execute('INSERT INTO sessions(id,expires_at,data) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET expires_at=excluded.expires_at,data=excluded.data',
                    (sid, expires_at, json.dumps(data)))


def delete_session(sid):
    with LOCK, connection() as con:
        con.execute('DELETE FROM sessions WHERE id=?', (sid,))


def invalidate_sessions(keep_local=False):
    with LOCK, connection() as con:
        if not keep_local:
            con.execute('DELETE FROM sessions')
            return
        for row in con.execute('SELECT id,data FROM sessions').fetchall():
            session = json.loads(row['data'])
            if session.get('local_access'):
                for key in ('user', 'oauth', 'identity_fetched_at', 'auth_error'):
                    session.pop(key, None)
                con.execute('UPDATE sessions SET data=? WHERE id=?', (json.dumps(session), row['id']))
            else:
                con.execute('DELETE FROM sessions WHERE id=?', (row['id'],))


def delete_expired_sessions(now=None):
    with LOCK, connection() as con:
        con.execute('DELETE FROM sessions WHERE expires_at<=?', (time.time() if now is None else now,))


def secret_get(name):
    with connection() as con:
        row = con.execute('SELECT value FROM secrets WHERE name=?', (name,)).fetchone()
    return row['value'] if row else None


def secret_put(name, value):
    with LOCK, connection() as con:
        con.execute('INSERT INTO secrets(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value', (name, value))


def secret_delete(name):
    with LOCK, connection() as con:
        con.execute('DELETE FROM secrets WHERE name=?', (name,))


def purge_google_authorization_data():
    """Erase API-derived identity/upload data, retaining only the user's own media.

    A local boolean prevents repeat publication after the remote ID is erased.
    No original, transcript, generated/user-written metadata, or media file is removed.
    """
    with LOCK:
        secret_delete('google_token')
        secret_delete('local_google_identity')
        invalidate_sessions(keep_local=os.environ.get('RECORDER_MODE') == 'local')
        for job in list_jobs():
            had_transfer = bool(job.get('video_id') or job.get('_upload_url') or job.get('publication_locked'))
            for key in ('video_id', 'video_url', 'actual_privacy', '_upload_url', '_upload_bytes', '_upload_total', '_next_poll', '_api_fetched_at', '_intended_channel'):
                job.pop(key, None)
            if had_transfer:
                job.update(publication_locked=True, state='disconnected', progress=0, error=None,
                           message='已解除 YouTube 連接並清除上傳記錄；為避免重複發布，請到 YouTube 工作室核查原影片')
                job.pop('_resume_state', None)
            elif job.get('state') in ('publish_queued', 'publishing', 'youtube_processing'):
                job.update(state='failed', error='YouTube 授權已解除，請重新連接後重試', _resume_state='publish_queued')
            save_job(job)


def expire_historical_api_data(cutoff):
    """Caller holds the publisher lock. Never touches a media file or makes HTTP calls."""
    removed = 0
    with LOCK:
        for job in list_jobs():
            if not any(job.get(k) is not None for k in ('video_id', 'video_url', 'actual_privacy', '_upload_url')):
                continue
            if job.get('_api_fetched_at', 0) > cutoff:
                continue
            # An old upload that is not currently executing is locked before its remote references are erased.
            job.update(publication_locked=True, state='records_cleared', progress=0, error=None,
                       message='已依資料保留期限清除 YouTube 上傳記錄；影片仍保留，請到 YouTube 工作室查看或管理')
            for key in ('video_id', 'video_url', 'actual_privacy', '_upload_url', '_upload_bytes', '_upload_total', '_next_poll', '_api_fetched_at', '_resume_state'):
                job.pop(key, None)
            save_job(job)
            removed += 1
    return removed


def refresh_existing_identities(user, checked_at):
    """Refresh API identity data only for already authenticated, unexpired devices."""
    with LOCK, connection() as con:
        for row in con.execute('SELECT id,data FROM sessions WHERE expires_at>?', (checked_at,)).fetchall():
            session = json.loads(row['data'])
            previous = session.get('user', {})
            if previous.get('email') != user['email'] or previous.get('channel_id') != user['channel_id']:
                continue
            session.update(user=user, identity_fetched_at=checked_at)
            session.pop('auth_error', None)
            # Background upkeep does not extend the device's own expiry or sign in visitors.
            con.execute('UPDATE sessions SET data=? WHERE id=?', (json.dumps(session), row['id']))


def expire_stale_identities(cutoff):
    with LOCK, connection() as con:
        for row in con.execute('SELECT id,data FROM sessions').fetchall():
            session = json.loads(row['data'])
            if session.get('user') and session.get('identity_fetched_at', 0) <= cutoff:
                con.execute('DELETE FROM sessions WHERE id=?', (row['id'],))
