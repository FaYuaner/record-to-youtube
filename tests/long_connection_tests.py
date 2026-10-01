"""Focused isolated checks: no real credentials, media or Google calls."""
from pathlib import Path
import os,sys,time,json,uuid,unittest
from unittest.mock import patch,Mock
from contextlib import contextmanager
BASE=Path(__file__).resolve().parents[1]
RUN=BASE/'long-connection-tests'/uuid.uuid4().hex;RUN.mkdir(parents=True)
os.environ.update(RECORDER_DATA_DIR=str(RUN),RECORDER_WORKER_ENABLED='false',PROXY_MANAGED='0')
sys.path.insert(0,str(BASE/'server'))
import auth,db,app
from fastapi.testclient import TestClient
class Tests(unittest.TestCase):
 def setUp(self):
  db.init();auth.init();db.invalidate_sessions();db.secret_delete('google_token');db.secret_delete('authorization_notice')
  self.now=time.time();self.user={'email':auth.OWNER_EMAIL,'channel_id':auth.OWNER_CHANNEL,'channel_title':'Old title'}
  self.sid=uuid.uuid4().hex;db.save_session(self.sid,{'csrf_token':'fixture','user':self.user,'identity_fetched_at':self.now-40*86400})
  self.block=patch.object(auth,'google_client',side_effect=AssertionError('Live network forbidden'));self.block.start();self.addCleanup(self.block.stop)
 def token(self,**kwargs):
  t={'access_token':'fixture-only','refresh_token':'fixture-only','expires_at':self.now-1,'_confirmed_at':self.now-40*86400};t.update(kwargs);db.secret_put('google_token',auth.encrypt(json.dumps(t)))
 def test_idle_connection_is_rechecked_not_deleted(self):
  self.token()
  def refresh(s):s.update(user={**self.user,'channel_title':'New title'},identity_fetched_at=self.now)
  with patch.object(auth,'refresh_identity',side_effect=refresh):self.assertTrue(auth.maintain_authorization(self.now))
  db.expire_stale_identities(self.now-29*86400)
  self.assertEqual(db.get_session(self.sid)['user']['channel_title'],'New title');self.assertTrue(auth.load_token())
  with patch.object(auth,'refresh_identity') as r:self.assertFalse(auth.maintain_authorization(self.now+120));r.assert_not_called()
 def test_transient_outage_preserves_token_and_retries(self):
  self.token()
  with patch.object(auth,'refresh_identity',side_effect=TimeoutError):self.assertFalse(auth.maintain_authorization(self.now))
  self.assertTrue(auth.load_token());self.assertEqual(auth.load_token()['_maintenance_retry_at'],self.now+3600)
  with patch.object(auth,'refresh_identity') as r:self.assertFalse(auth.maintain_authorization(self.now+60));r.assert_not_called()
 def test_google_revocation_still_clears_auth(self):
  self.token()
  @contextmanager
  def client():yield Mock(post=Mock(return_value=Mock(status_code=400,json=lambda:{'error':'invalid_grant'})))
  with patch.object(auth,'google_client',client),patch.object(auth,'client_config',return_value={'client_id':'fixture','client_secret':'fixture'}):self.assertFalse(auth.maintain_authorization(self.now))
  self.assertFalse(auth.load_token());self.assertIsNone(db.get_session(self.sid))
 def test_visitors_are_never_signed_in_by_maintenance(self):
  sid='visitor';db.save_session(sid,{'csrf_token':'visitor'});db.refresh_existing_identities(self.user,self.now)
  self.assertNotIn('user',db.get_session(sid))
  c=TestClient(app.app,base_url='https://recorder.example.test');self.assertFalse(c.get('/recorder/api/session').json()['authenticated']);self.assertEqual(c.get('/recorder/api/jobs').status_code,401)
 def test_old_valid_cookie_is_supported_and_renewed(self):
  with patch('itsdangerous.timed.time.time',return_value=self.now-40*86400):cookie=auth.sign_sid(self.sid)
  self.assertEqual(auth.read_sid(cookie),self.sid)
  db.refresh_existing_identities(self.user,self.now)
  c=TestClient(app.app,base_url='https://recorder.example.test');c.cookies.set(app.COOKIE,cookie)
  r=c.get('/recorder/api/session');self.assertTrue(r.json()['authenticated']);self.assertIn('Max-Age=31536000',r.headers['set-cookie'])
 def test_stale_identity_and_normal_api_retention_still_expire(self):
  db.expire_stale_identities(self.now-29*86400);self.assertIsNone(db.get_session(self.sid))
  j={'id':'old','client_id':'old','created_at':self.now-40*86400,'video_id':'fixture','_api_fetched_at':self.now-40*86400};db.save_job(j)
  db.expire_historical_api_data(self.now-29*86400);self.assertIsNone(db.get_job('old').get('video_id'))
if __name__=='__main__':unittest.main()
