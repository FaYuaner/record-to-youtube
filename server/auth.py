"""Owner-only Google OAuth with PKCE, state, OIDC nonce and encrypted tokens."""
import base64
import hashlib
import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from cryptography.fernet import Fernet
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import db
from network import network_access

BASE_URL = os.environ.get('RECORDER_BASE_URL', 'http://127.0.0.1:18487/recorder').strip().rstrip('/')
LOCAL_MODE = os.environ.get('RECORDER_MODE', 'remote').lower() == 'local'
CALLBACK = BASE_URL + '/api/oauth/callback'
OWNER_EMAIL = os.environ.get('OWNER_EMAIL', '').strip().lower()
OWNER_CHANNEL = os.environ.get('OWNER_CHANNEL_ID', '').strip()
GOOGLE_PROXY = os.environ.get('GOOGLE_HTTP_PROXY', os.environ.get('PROXY_URL', '')).strip()
SCOPES = 'openid email https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly'
SESSION_SECONDS = db.SESSION_SECONDS
TOKEN_LOCK = threading.RLock()
_cipher = None
_signer = None


def validate_settings():
    if LOCAL_MODE:
        parsed = urlparse(BASE_URL)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path != '/recorder'):
            raise RuntimeError('本机模式只能使用 http://127.0.0.1:<端口>/recorder')
        return
    missing = []
    if not os.environ.get('RECORDER_BASE_URL', '').strip():
        missing.append('RECORDER_BASE_URL')
    if not OWNER_EMAIL:
        missing.append('OWNER_EMAIL')
    if not OWNER_CHANNEL:
        missing.append('OWNER_CHANNEL_ID')
    if missing:
        raise RuntimeError('请先配置自己的服务器、Google 账号与频道：' + ', '.join(missing))
    parsed = urlparse(BASE_URL)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not parsed.path.rstrip('/') or '%' in parsed.path
            or any(part in ('.', '..') for part in parsed.path.split('/'))):
        raise RuntimeError('RECORDER_BASE_URL 必须是包含服务路径的 HTTPS 地址，不能包含账号、查询参数或片段')
    if '@' not in OWNER_EMAIL or any(c.isspace() for c in OWNER_EMAIL) or OWNER_EMAIL == '*':
        raise RuntimeError('OWNER_EMAIL 必须是允许登录的完整 Google 账号邮箱')


def local_identity():
    value = db.secret_get('local_google_identity') if LOCAL_MODE else None
    return json.loads(decrypt(value)) if value else {}


def owner_email():
    return OWNER_EMAIL or (local_identity().get('email', '') if LOCAL_MODE else '')


def owner_channel():
    return OWNER_CHANNEL or (local_identity().get('channel_id', '') if LOCAL_MODE else '')


def job_owner():
    # Local files belong to the device vault, including recordings made offline.
    return '__local_device__' if LOCAL_MODE else OWNER_EMAIL


def local_access_key():
    if not LOCAL_MODE:
        raise RuntimeError('本机访问仅在桌面模式可用')
    return key_file('local-access.key', lambda: secrets.token_urlsafe(48).encode()).decode()


def key_file(name, create):
    path = db.DATA_DIR / name
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(create())
    if os.name != 'nt' and path.stat().st_mode & 0o077:
        raise RuntimeError(f'{name} permissions must be 0600')
    return path.read_bytes()


def init():
    global _cipher, _signer
    _cipher = Fernet(key_file('token.key', Fernet.generate_key))
    _signer = URLSafeTimedSerializer(key_file('session.key', lambda: secrets.token_bytes(48)), salt='recorder-owner-session')


def encrypt(value):
    return _cipher.encrypt(value.encode()).decode()


def decrypt(value):
    return _cipher.decrypt(value.encode()).decode()


def sign_sid(sid):
    return _signer.dumps(sid)


def read_sid(cookie):
    if not cookie:
        return None
    try:
        return _signer.loads(cookie, max_age=SESSION_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


def client_config():
    path = os.environ.get('GOOGLE_CLIENT_SECRET_FILE')
    if path:
        from pathlib import Path
        p = Path(path)
        if os.name != 'nt' and p.stat().st_mode & 0o077:
            raise RuntimeError('OAuth credential file permissions must be 0600')
        raw = json.loads(p.read_text(encoding='utf-8-sig'))
        if LOCAL_MODE and 'installed' not in raw:
            raise RuntimeError('本机上传需要 Google 桌面应用 OAuth JSON；请在本机配置 GOOGLE_CLIENT_SECRET_FILE')
        conf = raw.get('installed' if LOCAL_MODE else 'web', raw)
    else:
        if LOCAL_MODE:
            raise RuntimeError('连接 YouTube 前，请在 desktop/local.env 的 GOOGLE_CLIENT_SECRET_FILE 配置自己的桌面应用 OAuth JSON')
        conf = {'client_id': os.environ.get('GOOGLE_CLIENT_ID'), 'client_secret': os.environ.get('GOOGLE_CLIENT_SECRET')}
    if not conf.get('client_id') or not conf.get('client_secret'):
        raise RuntimeError('尚未設定 Google 授權憑證')
    return conf


def configured():
    try:
        validate_settings()
        client_config()
        return True
    except (OSError, ValueError, RuntimeError):
        return False


@contextmanager
def google_client():
    # Explicit proxy only; never inherit a global proxy or bypass TLS validation.
    with network_access():
        with httpx.Client(proxy=GOOGLE_PROXY or None, trust_env=False, timeout=httpx.Timeout(60, connect=15), follow_redirects=False) as client:
            yield client


def begin(session):
    validate_settings()
    conf = client_config()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    nonce = secrets.token_urlsafe(32)
    session['oauth'] = {'state': state, 'verifier': verifier, 'nonce': nonce, 'created_at': time.time()}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': conf['client_id'], 'redirect_uri': CALLBACK, 'response_type': 'code',
        'scope': SCOPES, 'state': state, 'nonce': nonce, 'code_challenge': challenge,
        'code_challenge_method': 'S256', 'access_type': 'offline', 'prompt': 'consent',
        'login_hint': owner_email(), 'include_granted_scopes': 'false',
    })


def finish(code, state, session):
    validate_settings()
    pending = session.pop('oauth', None)
    if not pending or time.time() - pending['created_at'] > 600 or not secrets.compare_digest(state or '', pending['state']):
        raise ValueError('授權請求已過期，請重新連接')
    conf = client_config()
    with google_client() as client:
        res = client.post('https://oauth2.googleapis.com/token', data={
            'code': code, 'client_id': conf['client_id'], 'client_secret': conf['client_secret'],
            'redirect_uri': CALLBACK, 'grant_type': 'authorization_code', 'code_verifier': pending['verifier'],
        })
        if res.status_code != 200:
            raise ValueError('Google 授權交換失敗，請重新連接')
        token = res.json()
        encoded = token.get('id_token', '')
        header = jwt.get_unverified_header(encoded)
        keys_response = client.get('https://www.googleapis.com/oauth2/v3/certs')
        keys_response.raise_for_status()
        keys = keys_response.json()['keys']
        jwk = next((key for key in keys if key.get('kid') == header.get('kid')), None)
        if not jwk:
            raise ValueError('無法驗證 Google 登入簽章')
        claims = jwt.decode(encoded, jwt.PyJWK.from_dict(jwk).key, algorithms=['RS256'], audience=conf['client_id'],
                            issuer=['https://accounts.google.com', 'accounts.google.com'], options={'require': ['exp', 'iat', 'sub', 'aud', 'iss']})
        if not secrets.compare_digest(claims.get('nonce', ''), pending['nonce']):
            raise ValueError('Google 登入驗證不符')
        email = claims.get('email', '').lower()
        if not email or claims.get('email_verified') is not True or (owner_email() and email != owner_email()):
            raise ValueError('此工具僅供指定的頻道擁有人使用')
        res = client.get('https://www.googleapis.com/youtube/v3/channels', params={'part': 'snippet', 'mine': 'true'},
                         headers={'Authorization': 'Bearer ' + token['access_token']})
        if res.status_code != 200:
            raise ValueError('未能取得 YouTube 頻道，請同意頻道讀取和上傳權限')
        channels = res.json().get('items', [])
        channel = (next((item for item in channels if item['id'] == owner_channel()), None)
                   if owner_channel() else channels[0] if LOCAL_MODE and len(channels) == 1 else None)
        if not channel:
            raise ValueError('授權頻道不符，請選擇已設定的 YouTube 頻道重新連接')
    previous = load_token()
    if not token.get('refresh_token') and previous:
        token['refresh_token'] = previous.get('refresh_token')
    if not token.get('refresh_token'):
        raise ValueError('未取得背景工作授權，請重新連接 Google')
    token['expires_at'] = time.time() + int(token.get('expires_in', 3600))
    token['_confirmed_at'] = time.time()
    token.pop('id_token', None)
    user = {'email': email, 'channel_id': channel['id'], 'channel_title': channel.get('snippet', {}).get('title', 'YouTube')}
    # Identity display belongs to the expiring session, not the long-lived token vault.
    token.pop('user', None)
    with TOKEN_LOCK:
        if LOCAL_MODE:
            enrolled = local_identity()
            if enrolled and (enrolled.get('email') != user['email'] or enrolled.get('channel_id') != user['channel_id']):
                raise ValueError('此安装已连接另一个账号或频道，请使用独立的数据目录')
            db.secret_put('local_google_identity', encrypt(json.dumps(user)))
        db.secret_put('google_token', encrypt(json.dumps(token)))
        db.secret_delete('authorization_notice')
    session['user'] = user
    session['identity_fetched_at'] = time.time()
    return user


def load_token():
    value = db.secret_get('google_token')
    return json.loads(decrypt(value)) if value else None


def access_token():
    with TOKEN_LOCK:
        token = load_token()
        if not token:
            raise ValueError('請先連接 YouTube 頻道')
        if token.get('expires_at', 0) < time.time() + 120:
            conf = client_config()
            with google_client() as client:
                res = client.post('https://oauth2.googleapis.com/token', data={
                    'client_id': conf['client_id'], 'client_secret': conf['client_secret'],
                    'grant_type': 'refresh_token', 'refresh_token': token['refresh_token'],
                })
            if res.status_code != 200:
                try:
                    revoked = res.json().get('error') == 'invalid_grant'
                except ValueError:
                    revoked = False
                if revoked:
                    db.purge_google_authorization_data()
                    db.secret_put('authorization_notice', 'Google 授權已到期或已被撤銷，請重新連接頻道；已保存的錄影仍保留')
                if revoked:
                    raise ValueError('YouTube 授權已失效，請重新連接頻道')
                raise ValueError('Google 暫時無法更新連接，請稍後重試；原有授權仍保留')
            token.update(res.json())
            token.pop('user', None)
            token['expires_at'] = time.time() + int(token.get('expires_in', 3600))
            token['_confirmed_at'] = time.time()
            db.secret_put('google_token', encrypt(json.dumps(token)))
        return token['access_token']


def refresh_identity(session):
    """Refresh an already authenticated browser's channel; never signs in a new visitor."""
    user = session.get('user')
    if not user or user.get('email') != owner_email() or user.get('channel_id') != owner_channel():
        raise ValueError('請先連接指定的 YouTube 頻道')
    headers = {'Authorization': 'Bearer ' + access_token()}
    with google_client() as client:
        response = client.get('https://www.googleapis.com/youtube/v3/channels',
                              params={'part': 'snippet', 'mine': 'true'}, headers=headers)
    response.raise_for_status()
    channel = next((item for item in response.json().get('items', []) if item.get('id') == owner_channel()), None)
    if not channel:
        raise ValueError('目前授權無法存取指定頻道，請重新連接')
    session['user'] = {'email': owner_email(), 'channel_id': owner_channel(),
                        'channel_title': channel.get('snippet', {}).get('title', 'YouTube')}
    session['identity_fetched_at'] = time.time()


def disconnect():
    with TOKEN_LOCK:
        token = load_token()
        if token:
            with google_client() as client:
                res = client.post('https://oauth2.googleapis.com/revoke', data={'token': token.get('refresh_token') or token['access_token']})
            try:
                already_revoked = res.status_code == 400 and res.json().get('error') == 'invalid_token'
            except ValueError:
                already_revoked = False
            if res.status_code != 200 and not already_revoked:
                raise ValueError('Google 撤銷授權暫時失敗，請重試；也可前往 Google 帳戶權限頁撤銷')
        db.purge_google_authorization_data()
        db.secret_delete('authorization_notice')


def maintain_authorization(now=None):
    """Keep the owner's requested connection active; recheck Google daily, not by deleting tokens."""
    current = time.time() if now is None else now
    with TOKEN_LOCK:
        token = load_token()
        if not token or token.get('_identity_checked_at', 0) > current - 86400 or token.get('_maintenance_retry_at', 0) > current:
            return False
        try:
            session = {'user': {'email': owner_email(), 'channel_id': owner_channel()}}
            refresh_identity(session)
            latest = load_token()
            if not latest:
                return False
            latest['_identity_checked_at'] = current
            latest.pop('_maintenance_retry_at', None)
            db.secret_put('google_token', encrypt(json.dumps(latest)))
            db.refresh_existing_identities(session['user'], current)
            db.secret_delete('authorization_notice')
            return True
        except Exception:
            # invalid_grant is handled by access_token; transient outages retain the grant.
            latest = load_token()
            if latest:
                latest['_maintenance_retry_at'] = current + 3600
                db.secret_put('google_token', encrypt(json.dumps(latest)))
                db.secret_put('authorization_notice', 'Google 連接暫時無法更新，稍後會自動重試。')
            return False
