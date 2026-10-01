"""Run existing regression checks without external services or bulk file cleanup."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SKIP = (
    'test_cleanup_keeps_text_other_jobs_unknown_files_and_remote_reference',
    'test_cleanup_failure_is_visible_and_retry_does_not_upload_again',
    'test_youtube_processed_success_triggers_cleanup_and_correct_percent',
)
GROUPS = (
    ('tests/self_hosted_tests.py', 'SelfHostedTests'),
    ('server/media_tests.py', 'TimelineTests'),
    ('tests/long_connection_tests.py', 'Tests'),
    ('tests/upgrade_tests.py', 'UpgradeTests'),
    ('tests/recovery_tests.py', 'Recovery'),
)


def main():
    failed = False
    for relative, case in GROUPS:
        env = os.environ.copy()
        for key in list(env):
            if key.startswith(('GOOGLE_CLIENT_', 'DAIGUI_TEXT_', 'METADATA_')):
                env.pop(key)
        env.update(
            PYTHONDONTWRITEBYTECODE='1',
            RECORDER_DATA_DIR=tempfile.mkdtemp(prefix='recorder-offline-'),
            RECORDER_WORKER_ENABLED='false', PROXY_MANAGED='0',
            RECORDER_BASE_URL='https://recorder.example.test/recorder',
            OWNER_EMAIL='owner@example.test', OWNER_CHANNEL_ID='UCabcdefghijklmnopqrstuv',
            GOOGLE_HTTP_PROXY='', PROXY_URL='',
            ALLOW_PUBLIC_PUBLISH='true', DAIGUI_ALLOW_MODEL_DOWNLOAD='0',
        )
        code = f'''
import importlib.util, ipaddress, socket, sys, unittest
from pathlib import Path
root = Path({str(ROOT)!r})
sys.path.insert(0, str(root / 'server'))
def no_network(*args, **kwargs):
    raise AssertionError('External network forbidden in offline tests')
socket.create_connection = no_network
original_connect = socket.socket.connect
def guarded_connect(sock, address):
    # Windows asyncio builds its internal socketpair over loopback. Permit only
    # that standard-library caller, never application or API connections.
    caller = sys._getframe(1)
    if (caller.f_code.co_name == '_fallback_socketpair'
            and caller.f_code.co_filename == socket.__file__
            and isinstance(address, tuple)
            and ipaddress.ip_address(address[0]).is_loopback):
        return original_connect(sock, address)
    return no_network(sock, address)
socket.socket.connect = guarded_connect
socket.socket.connect_ex = no_network
spec = importlib.util.spec_from_file_location('offline_suite', root / {relative!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
suite = unittest.TestSuite(t for t in unittest.defaultTestLoader.loadTestsFromTestCase(getattr(module, {case!r}))
                           if t._testMethodName not in {SKIP!r})
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
'''
        print(f'Running {relative}: {case}', flush=True)
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=ROOT, env=env)
        failed = failed or result.returncode != 0
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
