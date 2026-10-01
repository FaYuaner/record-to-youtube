"""Deployment ownership checks. No real OAuth, server or browser activity."""
import asyncio
import os
from pathlib import PurePosixPath
import time
from types import SimpleNamespace
import uuid
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import app
import auth
import db
import network
from fastapi.testclient import TestClient


class SelfHostedTests(TestCase):
    def _client(self):
        db.init()
        (db.DATA_DIR / 'jobs').mkdir(exist_ok=True)
        auth.init()
        sid = 'fixture-' + uuid.uuid4().hex
        db.save_session(sid, {'csrf_token': 'fixture-only',
            'identity_fetched_at': time.time(),
            'user': {'email': auth.OWNER_EMAIL, 'channel_id': auth.OWNER_CHANNEL}})
        client = TestClient(app.app, base_url='https://recorder.example.test')
        client.cookies.set(app.COOKIE, auth.sign_sid(sid))
        client.headers['Origin'] = app.ORIGIN
        return client

    def test_new_installation_requires_review_before_youtube_upload(self):
        client = self._client()
        session = client.get('/recorder/api/session').json()
        self.assertEqual(session['default_privacy'], 'private')
        self.assertFalse(session['default_auto_publish'])
        with patch.object(app.shutil, 'disk_usage', return_value=SimpleNamespace(free=50 * 1024**3)):
            response = client.post('/recorder/api/jobs', headers={'X-CSRF-Token': 'fixture-only'},
                json={'client_id': str(uuid.uuid4()), 'filename': 'sample.webm', 'total_bytes': 1})
        self.assertEqual(response.status_code, 200, response.text)
        job = db.get_job(response.json()['id'])
        self.assertEqual(job['privacy'], 'private')
        self.assertFalse(job['auto_publish'])

    def test_explicit_upload_settings_remain_available(self):
        client = self._client()
        with patch.object(app, 'ALLOW_PUBLIC', True), \
                patch.object(app.shutil, 'disk_usage', return_value=SimpleNamespace(free=50 * 1024**3)):
            response = client.post('/recorder/api/jobs', headers={'X-CSRF-Token': 'fixture-only'},
                json={'client_id': str(uuid.uuid4()), 'filename': 'sample.webm', 'total_bytes': 1,
                      'privacy': 'public', 'auto_publish': True})
        self.assertEqual(response.status_code, 200, response.text)
        job = db.get_job(response.json()['id'])
        self.assertEqual(job['privacy'], 'public')
        self.assertTrue(job['auto_publish'])

    def test_worker_does_not_start_without_explicit_enablement(self):
        with patch.dict(os.environ), patch.object(app, 'recover_interrupted_jobs') as recover:
            os.environ.pop('RECORDER_WORKER_ENABLED', None)
            self.assertFalse(app.ensure_worker())
            recover.assert_not_called()
            os.environ['RECORDER_WORKER_ENABLED'] = 'true'
            self.assertTrue(app.worker_enabled())

    def test_managed_proxy_needs_own_settings_before_service_commands(self):
        with patch.dict(os.environ, {'PROXY_MANAGED': '1', 'PROXY_SERVICE_NAME': '',
                                    'PROXY_URL': 'http://127.0.0.1:8080'}), \
                patch.object(network.os, 'name', 'posix'), \
                patch.object(network, 'Path', PurePosixPath), \
                patch.object(network.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'PROXY_SERVICE_NAME'):
                with network.network_access():
                    self.fail('Managed proxy started without configuration')
            run.assert_not_called()

    def test_managed_proxy_uses_configured_service_and_port(self):
        with patch.dict(os.environ, {'PROXY_SERVICE_NAME': 'my-recorder-proxy.service',
                                    'PROXY_URL': 'http://127.0.0.1:8123',
                                    'PROXY_LOCK_FILE': '/tmp/my-recorder-proxy.lock'}), \
                patch.object(network.os, 'name', 'posix'), \
                patch.object(network, 'Path', PurePosixPath):
            unit, lock, host, port = network.managed_proxy_settings()
        self.assertEqual((unit, str(lock), host, port),
            ('my-recorder-proxy.service', '/tmp/my-recorder-proxy.lock', '127.0.0.1', 8123))

    def test_api_direct_route_is_an_explicit_deployment_choice(self):
        settings = {'PROXY_MANAGED': '0', 'PROXY_URL': 'http://127.0.0.1:8123', 'DIRECT_API_HOSTS': ''}
        with patch.dict(os.environ, settings), \
                patch.object(network.requests.Session, 'request', return_value='fixture') as request:
            session = network.ManagedProxySession()
            session.get('https://api.vendor.example.test/v1/messages')
            self.assertNotIn('proxies', request.call_args.kwargs)
            self.assertEqual(session.proxies['https'], settings['PROXY_URL'])
            os.environ['DIRECT_API_HOSTS'] = 'api.vendor.example.test'
            session.get('https://api.vendor.example.test/v1/messages')
            self.assertEqual(request.call_args.kwargs['proxies'], {'http': None, 'https': None})

    def test_missing_configuration_blocks_startup_and_oauth(self):
        async def start():
            async with app.lifespan(app.app):
                self.fail('Unconfigured service started')

        with patch.dict(os.environ, {'RECORDER_BASE_URL': ''}), \
                patch.object(auth, 'OWNER_EMAIL', ''), patch.object(auth, 'OWNER_CHANNEL', ''), \
                patch.object(db, 'init') as initialize, patch.object(app, 'ensure_worker') as worker, \
                patch.object(auth, 'client_config') as credentials:
            with self.assertRaisesRegex(RuntimeError, 'RECORDER_BASE_URL'):
                asyncio.run(start())
            with self.assertRaises(RuntimeError):
                auth.begin({})
            initialize.assert_not_called()
            worker.assert_not_called()
            credentials.assert_not_called()

    def test_oauth_uses_only_the_configured_deployment_and_account(self):
        url = 'https://my-recorder.example.test/private-recorder'
        callback = url + '/api/oauth/callback'
        with patch.dict(os.environ, {'RECORDER_BASE_URL': url}), \
                patch.object(auth, 'BASE_URL', url), patch.object(auth, 'CALLBACK', callback), \
                patch.object(auth, 'OWNER_EMAIL', 'my-account@example.test'), \
                patch.object(auth, 'OWNER_CHANNEL', 'UCabcdefghijklmnopqrstuv'), \
                patch.object(auth, 'client_config', return_value={'client_id': 'fixture-only'}):
            request = parse_qs(urlparse(auth.begin({})).query)
            self.assertEqual(request['redirect_uri'], [callback])
            self.assertEqual(request['login_hint'], ['my-account@example.test'])

    def test_invalid_origin_configuration_is_rejected(self):
        for url in ['http://remote.example.test/recorder', 'https://owner:password@example.test/recorder',
                    'https://example.test/recorder?target=other', 'https://example.test/',
                    'https://example.test/recorder/../other']:
            with self.subTest(url=url), patch.dict(os.environ, {'RECORDER_BASE_URL': url}), \
                    patch.object(auth, 'BASE_URL', url):
                with self.assertRaises(RuntimeError):
                    auth.validate_settings()

    def test_another_account_cannot_read_recordings(self):
        db.init()
        auth.init()
        db.save_session('other-account', {'csrf_token': 'fixture-only',
            'user': {'email': 'another@example.test', 'channel_id': auth.OWNER_CHANNEL}})
        client = TestClient(app.app, base_url='https://recorder.example.test')
        client.cookies.set(app.COOKIE, auth.sign_sid('other-account'))
        self.assertEqual(client.get('/recorder/api/jobs').status_code, 401)
