"""Run a single-user desktop backend on loopback, without a remote server."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / 'desktop' / 'local.env'
load_dotenv(SETTINGS, override=True)
os.environ['RECORDER_MODE'] = 'local'
port = int(os.environ.get('RECORDER_LOCAL_PORT', '18487'))
if not 1024 <= port <= 65535:
    raise SystemExit('RECORDER_LOCAL_PORT must be 1024..65535')
os.environ['RECORDER_BASE_URL'] = f'http://127.0.0.1:{port}/recorder'
os.environ.setdefault('RECORDER_DATA_DIR', str(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'DaiguiRecorder' / 'data'))
os.environ.setdefault('RECORDER_WORKER_ENABLED', 'true')
os.environ.setdefault('RECORDER_PROCESSING_MODE', 'direct')

if __name__ == '__main__':
    import json
    import auth
    launch_path = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'DaiguiRecorder' / 'local-launch.json'
    launch_path.parent.mkdir(parents=True, exist_ok=True)
    launch_path.write_text(json.dumps({'url': os.environ['RECORDER_BASE_URL'] + '/#access=' + auth.local_access_key()}), encoding='utf-8')
    import uvicorn
    uvicorn.run('app:app', host='127.0.0.1', port=port, workers=1, log_level='warning')
