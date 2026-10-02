"""Local-only scanner regression tests. Fixtures remain in an isolated directory."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

SCANNER = Path(__file__).with_name('check_publication.py')

class PublicationChecks(unittest.TestCase):
    def setUp(self):
        # Keep fixtures; never recursively delete user or test files.
        self.fixture = Path(tempfile.mkdtemp(prefix='publication-check-'))
        self.root = self.fixture/'project'
        self.root.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Example')
        self.git('config', 'user.email', 'example@example.test')

    def git(self, *args):
        return subprocess.run(['git','-C',str(self.root),*args],check=True,capture_output=True)

    def scan(self, *args):
        result = subprocess.run([sys.executable,str(SCANNER),'--root',str(self.root),*args],capture_output=True,text=True)
        return result, json.loads(result.stdout)

    def test_safe_examples_pass(self):
        (self.root/'.env.example').write_text('API_KEY=\nSERVER_URL=https://example.com\n')
        result, report = self.scan('--worktree')
        self.assertEqual(result.returncode,0)
        self.assertTrue(report['passed'])

    def test_runtime_configuration_blocks(self):
        (self.root/'local.env').write_text('WORKER=true\n')
        result, report = self.scan('--worktree')
        self.assertEqual(result.returncode,1)
        self.assertEqual(report['findings'][0]['rule'],'private_runtime_file')

    def test_reserved_dummy_url_passes_but_other_login_blocks(self):
        path = self.root/'example.py'
        path.write_text('https://owner:password@example.test/recorder')
        result, report = self.scan('--worktree')
        self.assertEqual(result.returncode,0)
        path.write_text('https://'+'actual-account'+':'+'actual-password'+'@example.test/recorder')
        result, report = self.scan('--worktree')
        self.assertEqual(result.returncode,1)
        self.assertNotIn('actual-password', result.stdout)

    def test_secret_redacted_in_worktree_and_history(self):
        secret = 'gh' + 'p_' + 'z'*36
        (self.root/'config.py').write_text('token="'+secret+'"\n')
        result, report = self.scan('--worktree')
        self.assertEqual(result.returncode,1)
        self.assertNotIn(secret,result.stdout)
        self.git('add','config.py'); self.git('commit','-qm','Fixture')
        (self.root/'config.py').write_text('token=""\n')
        self.git('add','config.py'); self.git('commit','-qm','Remove fixture token')
        result, report = self.scan('--history')
        self.assertEqual(result.returncode,1)
        self.assertNotIn(secret,result.stdout)
        self.assertEqual(report['commits_checked'],2)

    def test_known_private_marker_redacted(self):
        marker = 'private.example.test'
        (self.root/'README.md').write_text('https://'+marker)
        markers = self.fixture/'markers.txt'; markers.write_text(marker)
        result, report = self.scan('--markers-file',str(markers))
        self.assertEqual(result.returncode,1)
        self.assertNotIn(marker,result.stdout)

    def test_private_markers_must_stay_outside(self):
        markers = self.root/'markers.txt'; markers.write_text('private')
        result, report = self.scan('--markers-file',str(markers))
        self.assertEqual(result.returncode,2)

    def test_zip_configuration_blocks(self):
        artifact = self.fixture/'source.zip'
        with zipfile.ZipFile(artifact,'w') as archive:
            archive.writestr('source/local.env','WORKER=true')
        result, report = self.scan('--artifact',str(artifact))
        self.assertEqual(result.returncode,1)
        self.assertEqual(report['coverage'],'zip')

    def test_path_exemption_does_not_hide_secrets(self):
        (self.root/'local.env').write_text('token="'+'gh'+'p_'+'x'*36+'"')
        result, report = self.scan('--allow-path','local.env')
        self.assertEqual(result.returncode,1)
        self.assertEqual(report['findings'][0]['rule'],'credential_signature')

if __name__ == '__main__':
    unittest.main()
