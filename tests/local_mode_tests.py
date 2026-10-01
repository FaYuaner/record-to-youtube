"""Offline local recording, OAuth binding, upload recovery and tool protocol checks."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import app
import auth
import db
import processing
from fastapi.testclient import TestClient


class LocalTests(TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for target, value in [(('LOCAL_MODE'), True), ('BASE_URL', 'http://127.0.0.1:18487/recorder'),
                              ('CALLBACK', 'http://127.0.0.1:18487/recorder/api/oauth/callback'),
                              ('OWNER_EMAIL', ''), ('OWNER_CHANNEL', '')]:
            self.stack.enter_context(patch.object(auth, target, value))
        self.stack.enter_context(patch.object(app, 'ORIGIN', 'http://127.0.0.1:18487'))
        self.stack.enter_context(patch.dict(os.environ, {'RECORDER_MODE': 'local', 'RECORDER_PROCESSING_MODE': 'direct',
                                                      'RECORDER_WORKER_ENABLED': 'false'}))
        db.init(); auth.init()
        db.secret_delete('google_token'); db.secret_delete('local_google_identity')
        (db.DATA_DIR / 'jobs').mkdir(exist_ok=True)
        self.client = TestClient(app.app, base_url='http://127.0.0.1:18487')
        self.client.headers['Origin'] = app.ORIGIN
        self.client.post('/recorder/api/local/open', headers={'X-Local-Access': auth.local_access_key()})
        response = self.client.get('/recorder/api/session')
        self.session = response.json()
        self.client.headers['X-CSRF-Token'] = self.session['csrf_token']

    def make_job(self, mode='direct', automatic=True):
        with patch.object(app.shutil, 'disk_usage', return_value=SimpleNamespace(free=50 * 1024**3)):
            response = self.client.post('/recorder/api/jobs', json={'client_id': str(uuid.uuid4()),
                'filename': 'recording.webm', 'total_bytes': 32, 'processing_mode': mode, 'auto_publish': automatic})
        self.assertEqual(response.status_code, 200, response.text)
        job = db.get_job(response.json()['id'])
        original = app.job_dir(job['id']) / 'original.webm'
        original.write_bytes(b'\x1aE\xdf\xa3' + b'x' * 28)
        job = db.update_job(job['id'], state='queued', original_ready=True)
        return job, original

    def test_records_without_server_oauth_ai_or_ffmpeg(self):
        self.assertTrue(self.session['authenticated'])
        self.assertFalse(self.session['youtube_connected'])
        self.assertTrue(self.session['default_auto_publish'])
        self.assertEqual(self.session['default_processing_mode'], 'direct')
        response = self.client.get('/recorder/api/session')
        self.assertNotIn('Secure', response.headers['set-cookie'])
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertEqual(self.client.get('/recorder/').status_code, 200)
        job, original = self.make_job()
        with patch('media_pipeline.process_job', side_effect=AssertionError('Unexpected editing')):
            app.process_media(job)
        prepared = db.get_job(job['id'])
        self.assertEqual(prepared['state'], 'publish_queued')
        self.assertEqual(prepared['title'], 'recording')
        self.assertEqual(prepared['description'], '')
        self.assertEqual(Path(prepared['_final_path']), original)
        self.assertFalse(prepared['cleanup_after_upload'])
        self.assertTrue(prepared['retain_original'])
        self.assertEqual(self.client.get(f"/recorder/api/jobs/{job['id']}/media").headers['content-type'], 'video/webm')

    def test_manual_upload_does_not_queue_automatic_publication(self):
        job, _ = self.make_job(automatic=False)
        app.process_media(job)
        self.assertEqual(db.get_job(job['id'])['state'], 'ready')
        response = self.client.patch(f"/recorder/api/jobs/{job['id']}", json={'title': 'My title', 'description': ''})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['state'], 'ready')
        self.assertFalse(response.json()['metadata_required'])

    def test_host_origin_and_csrf_prevent_other_sites_using_local_vault(self):
        outsider = TestClient(app.app, base_url='http://127.0.0.1:18487')
        self.assertFalse(outsider.get('/recorder/api/session').json()['authenticated'])
        self.assertEqual(outsider.get('/recorder/api/jobs').status_code, 401)
        self.assertEqual(outsider.post('/recorder/api/local/open', headers={'Origin': app.ORIGIN, 'X-Local-Access': 'wrong'}).status_code, 403)
        self.assertEqual(self.client.get('/recorder/api/session', headers={'Host': 'evil.example:18487'}).status_code, 403)
        self.assertEqual(self.client.get('/recorder/api/session', headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.get('/recorder/api/session', headers={'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
        self.assertEqual(self.client.post('/recorder/api/oauth/start', headers={'X-CSRF-Token': 'wrong'}, json={}).status_code, 403)
        with patch.object(auth, 'BASE_URL', 'http://0.0.0.0:18487/recorder'):
            with self.assertRaises(RuntimeError): auth.validate_settings()

    def test_desktop_oauth_uses_loopback_and_pkce_and_rejects_web_credential(self):
        credential = db.DATA_DIR / ('oauth-fixture-' + uuid.uuid4().hex + '.json')
        credential.write_text(json.dumps({'installed': {'client_id': 'fixture.apps.googleusercontent.com', 'client_secret': 'fixture-only'}}))
        credential.chmod(0o600)
        with patch.dict(os.environ, {'GOOGLE_CLIENT_SECRET_FILE': str(credential)}):
            pending = {}
            params = parse_qs(urlparse(auth.begin(pending)).query)
            self.assertEqual(params['redirect_uri'], [auth.CALLBACK])
            self.assertEqual(params['code_challenge_method'], ['S256'])
            self.assertTrue(pending['oauth']['nonce'])
            with self.assertRaisesRegex(ValueError, '過期'): auth.finish('code', 'wrong-state', pending)
            credential.write_text(json.dumps({'web': {'client_id': 'fixture', 'client_secret': 'fixture'}}))
            with self.assertRaisesRegex(RuntimeError, '桌面应用'): auth.client_config()

    def test_first_google_account_is_bound_and_other_account_is_rejected(self):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())); key['kid'] = 'fixture'
        session = {'oauth': {'state': 'state', 'verifier': 'verifier', 'nonce': 'nonce', 'created_at': time.time()}}
        email = 'first@example.test'
        def tokens(email):
            claims = {'email': email, 'email_verified': True, 'nonce': 'nonce', 'iss': 'https://accounts.google.com',
                      'sub': '123', 'aud': 'fixture', 'iat': time.time(), 'exp': time.time()+3600}
            return {'id_token': jwt.encode(claims, private_key, algorithm='RS256', headers={'kid': 'fixture'}),
                    'access_token': 'fixture-access', 'refresh_token': 'fixture-refresh'}
        class FakeClient:
            def post(inner, *args, **kwargs): return SimpleNamespace(status_code=200, json=lambda: tokens(email))
            def get(inner, url, **kwargs):
                body = {'keys': [key]} if url.endswith('/certs') else {'items': [{'id': 'UCfixture', 'snippet': {'title': 'Test channel'}}]}
                return SimpleNamespace(status_code=200, json=lambda: body, raise_for_status=lambda: None)
        @contextlib.contextmanager
        def client(): yield FakeClient()
        with patch.object(auth, 'client_config', return_value={'client_id': 'fixture', 'client_secret': 'fixture'}), patch.object(auth, 'google_client', client):
            user = auth.finish('code', 'state', session)
            self.assertEqual(user['email'], email)
            self.assertEqual(auth.owner_channel(), 'UCfixture')
            email = 'other@example.test'
            session = {'oauth': {'state': 'state', 'verifier': 'verifier', 'nonce': 'nonce', 'created_at': time.time()}}
            with self.assertRaisesRegex(ValueError, '擁有人'): auth.finish('code', 'state', session)
            self.assertEqual(auth.owner_email(), 'first@example.test')

    def test_direct_upload_retry_uses_same_youtube_session_and_webm_bytes(self):
        job, original = self.make_job()
        app.process_media(job)
        calls = []; fail_once = [True]
        class FakeClient:
            def post(inner, url, **kwargs):
                calls.append(('start', kwargs)); return SimpleNamespace(status_code=200, headers={'Location': 'https://www.googleapis.com/upload/session-fixture'})
            def put(inner, url, **kwargs):
                calls.append(('transfer', kwargs))
                if kwargs['headers']['Content-Length'] == '0': return SimpleNamespace(status_code=308, headers={})
                if fail_once[0]: fail_once[0] = False; raise ConnectionError('fixture interruption')
                return SimpleNamespace(status_code=200, json=lambda: {'id': 'fixtureVideo', 'status': {'privacyStatus': 'private'}})
        @contextlib.contextmanager
        def client(): yield FakeClient()
        with patch.object(auth, 'google_client', client), patch.object(auth, 'access_token', return_value='fixture'):
            with self.assertRaises(ConnectionError): app.upload_video(db.get_job(job['id']))
            app.upload_video(db.get_job(job['id']))
        self.assertEqual(sum(c[0]=='start' for c in calls), 1)
        self.assertEqual(calls[0][1]['headers']['X-Upload-Content-Type'], 'video/webm')
        self.assertEqual(calls[-1][1]['content'], original.read_bytes())
        self.assertEqual(db.get_job(job['id'])['video_id'], 'fixtureVideo')
        self.assertTrue(original.exists())

    def tool_config(self, script_text):
        fixture = db.DATA_DIR / ('tool-fixture-' + uuid.uuid4().hex)
        fixture.mkdir()
        script = fixture / 'tool.py'; script.write_text(script_text, encoding='utf-8')
        config = fixture / 'settings.json'
        config.write_text(json.dumps({'command': [sys.executable, str(script), '{request}'], 'timeout_seconds': 10}))
        self.stack.enter_context(patch.dict(os.environ, {'RECORDER_PROCESSOR_CONFIG': str(config)}))
        return config

    def test_channel_change_never_sends_queued_original_to_other_channel(self):
        job, _ = self.make_job()
        app.process_media(job)
        with patch.object(auth, 'owner_channel', return_value='UCdifferent'), patch.object(auth, 'google_client') as client:
            with self.assertRaisesRegex(ValueError, '频道已变更'): app.upload_video(db.get_job(job['id']))
            client.assert_not_called()

    def test_custom_tool_waits_and_validates_output_before_queueing(self):
        self.tool_config('''import json,sys,time
from pathlib import Path
r=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'));time.sleep(0.05)
out=Path(r['output_directory'])/'final.mp4';out.write_bytes(b'valid fixture')
Path(r['result_path']).write_text(json.dumps({'final_path':str(out),'title':'Edited title'}))
''')
        job, original = self.make_job('external'); source_bytes = original.read_bytes()
        real_run = subprocess.run
        def run(command, **kwargs):
            if command[0] == 'ffprobe':
                return SimpleNamespace(stdout=b'{"format":{"duration":"1"},"streams":[{"codec_type":"video"}]}')
            return real_run(command, **kwargs)
        with patch.object(processing.shutil, 'which', return_value='ffprobe'), patch.object(processing.subprocess, 'run', side_effect=run):
            app.process_media(job)
        prepared = db.get_job(job['id'])
        self.assertEqual(prepared['state'], 'publish_queued')
        self.assertEqual(prepared['title'], 'Edited title')
        self.assertEqual(original.read_bytes(), source_bytes)

    def test_failed_tool_cannot_reuse_stale_success_or_fallback_to_original(self):
        self.tool_config('raise SystemExit(1)')
        job, original = self.make_job('external'); output = app.job_dir(job['id']) / 'processed'; output.mkdir()
        (output / 'result.json').write_text('{"final_path":"old.mp4"}')
        with patch.object(processing.shutil, 'which', return_value='ffprobe'):
            with self.assertRaisesRegex(ValueError, '执行失败'): app.process_media(job)
        self.assertFalse((output / 'result.json').exists())
        self.assertFalse(db.get_job(job['id'])['final_ready'])
        self.assertTrue(original.exists())

    def test_custom_tool_cannot_return_file_outside_job(self):
        self.tool_config("import json,sys\nfrom pathlib import Path\nr=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))\nPath(r['result_path']).write_text(json.dumps({'final_path':'../../other.mp4'}))")
        job, original = self.make_job('external')
        with patch.object(processing.shutil, 'which', return_value='ffprobe'):
            with self.assertRaisesRegex(ValueError, '独立成片'): app.process_media(job)
        self.assertTrue(original.exists())

    def test_custom_tool_timeout_keeps_original_and_does_not_queue_upload(self):
        self.tool_config('import time; time.sleep(30)')
        job, original = self.make_job('external')
        with patch.object(processing.shutil, 'which', return_value='ffprobe'), patch.object(processing, 'run_tool', side_effect=subprocess.TimeoutExpired('tool', 1)):
            with self.assertRaisesRegex(ValueError, '超时'): app.process_media(job)
        self.assertFalse(db.get_job(job['id'])['final_ready'])
        self.assertTrue(original.exists())
