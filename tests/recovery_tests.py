import os,sys,tempfile,threading,time,json,unittest
from pathlib import Path
from unittest.mock import patch
BASE=Path(__file__).resolve().parents[1]
os.environ.update(RECORDER_DATA_DIR=tempfile.mkdtemp(prefix='recorder-recovery-'),RECORDER_WORKER_ENABLED='true',PROXY_MANAGED='0')
sys.path.insert(0,str(BASE/'server'))
import app,db
class Recovery(unittest.TestCase):
 def setUp(self):
  db.init();app.STOP.clear();app.WAKE.clear();app.WORKER_THREAD=None;app.ACTIVE_JOB=None;app.WORKER_ERROR=False
  self.no_network=patch.object(app.auth,'google_client',side_effect=AssertionError('network forbidden'));self.no_network.start()
  with db.connection() as c:c.execute('DELETE FROM jobs')
 def tearDown(self):
  app.STOP.set();app.WAKE.set()
  if app.WORKER_THREAD:app.WORKER_THREAD.join(2)
  self.no_network.stop()
 def job(self,**kwargs):
  j=dict(id='test',client_id='test',state='queued',created_at=time.time(),privacy='public',title='Title',description='Description',total_bytes=1,received_bytes=1)
  j.update(kwargs);db.save_job(j);root=app.job_dir(j['id']);root.mkdir(parents=True,exist_ok=True);(root/'original.webm').write_bytes(b'x');return j
 def test_outer_exception_survives_and_runs_queue(self):
  self.job();calls=[]
  def maintenance():
   calls.append(1)
   if len(calls)==1:raise RuntimeError('injected maintenance failure')
  def process(j):db.update_job(j['id'],state='ready');app.STOP.set()
  with patch.object(app,'maintain_stored_data',side_effect=maintenance),patch.object(app,'process_media',side_effect=process):
   app.ensure_worker();app.WORKER_THREAD.join(8)
  self.assertEqual(db.get_job('test')['state'],'ready');self.assertGreaterEqual(len(calls),2)
 def test_supervisor_recovers_exit_single_worker(self):
  count=[];ready=threading.Event()
  def target():
   count.append(1)
   if len(count)==1:return
   ready.set();app.STOP.wait(10)
  with patch.object(app,'worker',side_effect=target):
   app.ensure_worker();app.WORKER_THREAD.join(1)
   supervisor=threading.Thread(target=app.supervise_worker);supervisor.start()
   self.assertTrue(ready.wait(7))
   threads=[threading.Thread(target=app.ensure_worker) for _ in range(8)]
   for t in threads:t.start()
   for t in threads:t.join()
   self.assertEqual(len(count),2);app.STOP.set();supervisor.join(1)
 def test_clicks_do_not_duplicate_active_job(self):
  self.job();started=threading.Event();release=threading.Event();calls=[]
  def process(j):calls.append(j['id']);started.set();release.wait(3);db.update_job(j['id'],state='ready');app.STOP.set()
  with patch.object(app,'maintain_stored_data'),patch.object(app,'process_media',side_effect=process),patch.object(app,'own_job',side_effect=lambda r,i:db.get_job(i)):
   app.ensure_worker();self.assertTrue(started.wait(1))
   for _ in range(5):self.assertIn('正在處理',app.retry(None,'test')['retry_message'])
   release.set();app.WORKER_THREAD.join(2);self.assertEqual(calls,['test'])
 def test_existing_video_polls_without_upload(self):
  self.job(state='failed',video_id='existing');polled=[]
  def poll(j):polled.append(j['video_id']);db.update_job(j['id'],state='published');app.STOP.set()
  with patch.object(app,'maintain_stored_data'),patch.object(app,'own_job',side_effect=lambda r,i:db.get_job(i)),patch.object(app,'poll_video',side_effect=poll),patch.object(app,'upload_video',side_effect=AssertionError('duplicate upload')):
   app.retry(None,'test');app.WORKER_THREAD.join(2)
  self.assertEqual(polled,['existing'])
 def test_missing_original_is_reported(self):
  j=self.job();(app.job_dir('test')/'original.webm').unlink()
  with patch.object(app,'own_job',return_value=j):
   with self.assertRaises(app.HTTPException) as ctx:app.retry(None,'test')
  self.assertIn('原片缺失',ctx.exception.detail)
if __name__=='__main__':unittest.main()
