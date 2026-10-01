"""Isolated regression checks for the recorder upgrade. No live Google or media API calls."""
from pathlib import Path
import hashlib
import json
import os
import sys
import time
import unittest
from unittest.mock import patch
import uuid

BASE = Path(__file__).resolve().parents[1]
RUN = BASE / 'upgrade-test-artifacts' / uuid.uuid4().hex
RUN.mkdir(parents=True)
os.environ.update(RECORDER_DATA_DIR=str(RUN), RECORDER_WORKER_ENABLED='false', PROXY_MANAGED='0')
sys.path.insert(0, str(BASE / 'server'))
from fastapi.testclient import TestClient
import httpx
import app
import auth
import db
import media_pipeline as media
from media_cleanup import clean_uploaded_media


class UpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init(); auth.init(); (RUN / 'jobs').mkdir()

    def setUp(self):
        # Every external request is forbidden unless this individual check mocks it.
        self.no_network = patch.object(auth, 'google_client', side_effect=AssertionError('Live Google request forbidden'))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def owner(self, age=0):
        sid = uuid.uuid4().hex
        data = {'csrf_token': 'fixture-only', 'user': {'email': auth.OWNER_EMAIL, 'channel_id': auth.OWNER_CHANNEL,
                'channel_title': 'Fixture'}, 'identity_fetched_at': time.time() - age}
        db.save_session(sid, data)
        return sid, data

    def job(self, state='private', media_files=True):
        jid = str(uuid.uuid4()); root = RUN / 'jobs' / jid; root.mkdir(); (root / 'processed').mkdir()
        value = dict(id=jid, client_id=str(uuid.uuid4()), owner=auth.OWNER_EMAIL, state=state,
                     created_at=time.time(), title='保留的标题', description='保留的简介', total_bytes=10,
                     received_bytes=10, original_ready=True, final_ready=True, privacy='private',
                     auto_publish=True, video_id='fixture-id', _processed_video_id='fixture-id', _processed_at=time.time())
        db.save_job(value)
        if media_files:
            for name in ['original.webm', 'chunk-000000', 'processed/final.mp4', 'processed/segment-00000.mkv',
                         'processed/original-audio.wav', 'processed/final-audio.wav', 'processed/segments.ffconcat',
                         'processed/transcript.txt', 'processed/publication.json', 'private-notes.txt']:
                (root / name).write_bytes(b'fixture')
        return value, root

    def prepared(self, overrides, generator):
        root = RUN / uuid.uuid4().hex; root.mkdir(); source = root / 'original.webm'; source.write_bytes(b'fixture')
        out = root / 'processed'; out.mkdir(); final = out / 'final.mp4'; final.write_bytes(b'fixture-final')
        state = {'source': {'size': source.stat().st_size, 'mtime_ns': source.stat().st_mtime_ns,
                           'path_hash': hashlib.sha256(str(source.resolve()).encode()).hexdigest(), 'version': media.PIPELINE_VERSION},
                 'verified': True, 'stats': {'duration': 1}, 'final_signature': {'size': final.stat().st_size, 'mtime_ns': final.stat().st_mtime_ns},
                 'transcription': {'text': '实际口播', 'segments': [], 'language': 'zh'}}
        (out / 'media-manifest.json').write_text(json.dumps(state), encoding='utf-8')
        with patch.object(media, 'probe', return_value={'duration': 1}), patch.object(media, 'generate_metadata', generator):
            return media.process_job(source, out, metadata_overrides=overrides)

    def test_filled_fields_skip_generation_and_preserve_original_writing(self):
        from unittest.mock import Mock
        generator = Mock(side_effect=AssertionError('Must not generate'))
        result = self.prepared({'title': ' 我的简体标题 ', 'description': '我的简体介绍'}, generator)
        self.assertEqual(result['title'], ' 我的简体标题 ')
        self.assertEqual(result['description'], '我的简体介绍')
        self.assertFalse(result['metadata_required']); generator.assert_not_called()

    def test_generate_only_missing_fields(self):
        from unittest.mock import Mock
        for supplied in ({'title': '自填标题', 'description': ''}, {'title': '  ', 'description': '自填介绍'}):
            generator = Mock(return_value={'title': '生成標題', 'description': '生成簡介'})
            result = self.prepared(supplied, generator)
            for key in supplied:
                self.assertEqual(result[key], supplied[key] if supplied[key].strip() else generator.return_value[key])
            generator.assert_called_once_with('实际口播')

    def test_generation_failure_keeps_user_title_and_video(self):
        from unittest.mock import Mock
        result = self.prepared({'title': '真实标题', 'description': ''},
                               Mock(side_effect=media.MediaPipelineError('metadata_api', '服务不可用')))
        self.assertTrue(result['metadata_required']); self.assertEqual(result['title'], '真实标题')
        self.assertTrue(Path(result['final_path']).exists())

    def test_remembered_browser_survives_previous_twelve_hour_limit(self):
        sid, data = self.owner(age=2 * 86400)
        self.assertIsNotNone(db.get_session(sid))
        with patch.object(auth, 'refresh_identity', side_effect=lambda session: session.update(identity_fetched_at=time.time())) as refresh:
            client = TestClient(app.app, base_url='https://recorder.example.test')
            client.cookies.set(app.COOKIE, auth.sign_sid(sid))
            response = client.get('/recorder/api/session')
            self.assertTrue(response.json()['authenticated']); refresh.assert_called_once()
            self.assertIn('Max-Age=31536000', response.headers['set-cookie'])
            self.assertEqual(client.get('/recorder/api/jobs').status_code, 200)

    def test_unauthenticated_visitor_cannot_use_owner_connection(self):
        client = TestClient(app.app, base_url='https://recorder.example.test')
        self.assertFalse(client.get('/recorder/api/session').json()['authenticated'])
        self.assertEqual(client.get('/recorder/api/jobs').status_code, 401)

    def test_expired_identity_is_not_remembered_indefinitely(self):
        sid, _ = self.owner(age=30 * 86400)
        db.expire_stale_identities(time.time() - 29 * 86400)
        self.assertIsNone(db.get_session(sid))

    def test_logout_revokes_only_this_browser_session(self):
        sid, _ = self.owner(); client = TestClient(app.app, base_url='https://recorder.example.test')
        client.cookies.set(app.COOKIE, auth.sign_sid(sid))
        response = client.post('/recorder/api/logout', headers={'Origin': app.ORIGIN, 'X-CSRF-Token': 'fixture-only'}, json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get('/recorder/api/jobs').status_code, 401)

    def test_explicit_auto_upload_and_bad_metadata_rejected(self):
        sid, _ = self.owner(); client = TestClient(app.app, base_url='https://recorder.example.test')
        client.cookies.set(app.COOKIE, auth.sign_sid(sid)); headers={'Origin': app.ORIGIN, 'X-CSRF-Token': 'fixture-only'}
        payload = {'client_id': str(uuid.uuid4()), 'filename': 'fixture.webm', 'total_bytes': 10, 'title': '手填简体', 'auto_publish': True}
        response = client.post('/recorder/api/jobs', headers=headers, json=payload)
        self.assertEqual(response.status_code, 200); self.assertTrue(response.json()['auto_publish'])
        self.assertEqual(response.json()['title'], '手填简体')
        payload.update(client_id=str(uuid.uuid4()), description='界' * 1700)
        self.assertEqual(client.post('/recorder/api/jobs', headers=headers, json=payload).status_code, 422)

    def test_filling_missing_metadata_resumes_automatic_upload(self):
        job, root = self.job('ready'); job.pop('video_id'); job['metadata_required']=True; job['description']=''; db.save_job(job)
        sid, _=self.owner(); client=TestClient(app.app,base_url='https://recorder.example.test'); client.cookies.set(app.COOKIE,auth.sign_sid(sid))
        response=client.patch('/recorder/api/jobs/'+job['id'],headers={'Origin':app.ORIGIN,'X-CSRF-Token':'fixture-only'},json={'description':'我自己补齐的介绍'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['state'],'publish_queued')
        self.assertEqual(response.json()['description'],'我自己补齐的介绍')
        self.assertFalse(response.json()['metadata_required'])

    def test_cleanup_keeps_text_other_jobs_unknown_files_and_remote_reference(self):
        job, root = self.job(); other, other_root = self.job()
        clean_uploaded_media(job)
        self.assertFalse((root / 'original.webm').exists()); self.assertFalse((root / 'processed/final.mp4').exists())
        self.assertTrue((root / 'processed/transcript.txt').exists()); self.assertTrue((root / 'private-notes.txt').exists())
        self.assertTrue((other_root / 'original.webm').exists())
        result=db.get_job(job['id']); self.assertEqual(result['cleanup_state'], 'complete')
        self.assertEqual(result['video_id'], job['video_id']); self.assertEqual(result['title'], job['title'])
        self.assertFalse(result['original_ready']); self.assertFalse(result['final_ready'])
        clean_uploaded_media(result)  # idempotent, no duplicate deletion

    def test_cleanup_requires_verified_completed_video(self):
        for state in ['receiving', 'publishing', 'youtube_processing', 'failed']:
            job, root = self.job(state)
            with self.assertRaises(ValueError): clean_uploaded_media(job)
            self.assertTrue((root / 'original.webm').exists())
        job, root = self.job(); job['_processed_video_id']='different-id'
        with self.assertRaises(ValueError): clean_uploaded_media(job)
        self.assertTrue((root / 'original.webm').exists())

    def test_cleanup_failure_is_visible_and_retry_does_not_upload_again(self):
        job, root = self.job()
        with patch('media_cleanup.os.unlink', side_effect=PermissionError('fixture')): clean_uploaded_media(job)
        failed=db.get_job(job['id']); self.assertEqual(failed['cleanup_state'], 'failed')
        self.assertEqual(failed['state'], 'private'); self.assertTrue((root/'original.webm').exists())
        clean_uploaded_media(failed)
        self.assertEqual(db.get_job(job['id'])['cleanup_state'], 'complete')

    def test_active_processing_never_cleans_even_with_processed_upload_status(self):
        job, root=self.job('youtube_processing')
        response=httpx.Response(200,json={'items':[{'status':{'uploadStatus':'processed','privacyStatus':'private'}, 'processingDetails':{'processingStatus':'processing'}}]})
        from unittest.mock import MagicMock
        client=MagicMock();client.__enter__.return_value.get.return_value=response
        with patch.object(auth,'google_client',return_value=client), patch.object(app,'google_headers',return_value={}): app.poll_video(job)
        self.assertEqual(db.get_job(job['id'])['state'],'youtube_processing');self.assertTrue((root/'original.webm').exists())

    def test_youtube_processed_success_triggers_cleanup_and_correct_percent(self):
        job, root=self.job('youtube_processing')
        response=httpx.Response(200,json={'items':[{'status':{'uploadStatus':'processed','privacyStatus':'private'}, 'processingDetails':{'processingStatus':'succeeded'}}]})
        from unittest.mock import MagicMock
        client=MagicMock();client.__enter__.return_value.get.return_value=response
        with patch.object(auth,'google_client',return_value=client), patch.object(app,'google_headers',return_value={}): app.poll_video(job)
        result=app.public_job(db.get_job(job['id']))
        self.assertEqual(result['state'],'private');self.assertEqual(result['cleanup_state'],'complete')
        self.assertEqual(result['server_upload_progress'],1);self.assertEqual(result['youtube_upload_progress'],1)


if __name__=='__main__':
    unittest.main(verbosity=2)
