"""Redacted checks of Git worktrees, reachable history and source ZIPs."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import urlsplit
import zipfile

PATTERNS = {
    'credential_signature': re.compile(r'AIza[\w-]{35}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|GOCSPX-[A-Za-z0-9_-]{20,}|1//[A-Za-z0-9_-]{50,}|sk-(?:proj-)?[A-Za-z0-9_-]{40,}|AKIA[A-Z0-9]{16}'),
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'embedded_credential': re.compile(r'(?i)(?:client_secret|refresh_token|access_token|api_key|password)[\x22\x27]?\s*[=:]\s*[\x22\x27][A-Za-z0-9_+/-]{24,}[\x22\x27]'),
    'authenticated_url': re.compile(r'https?://[^\s/\x22\x27:]+:[^\s/@\x22\x27]+@[^/\s\x22\x27]+'),
}
PRIVATE_NAMES = {'local.env', 'recorder.config.json', 'processor.config.json'}
PRIVATE_SUFFIXES = {'.sqlite', '.sqlite3', '.db', '.key', '.pem', '.p12', '.pfx', '.bundle'}

def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    if result.returncode:
        raise ValueError('Git operation failed; verify the repository path and refs')
    return result.stdout

def is_private_file(name):
    path = PurePosixPath(name)
    base = path.name.lower()
    example = '.example' in base or '.sample' in base
    return (not example and (base in PRIVATE_NAMES or base == '.env' or
            (base.startswith('.env.') and base != '.env.example') or
            (base.startswith('client_secret') and base.endswith('.json')) or
            path.suffix.lower() in PRIVATE_SUFFIXES or
            any(p.lower() in {'recordings', 'chromeprofile', 'logs'} for p in path.parts) or
            ('data' in path.parts and path.suffix.lower() in {'.json', '.webm', '.mp4', '.sqlite3'})))

def inspect(name, raw, markers, allowed, findings, counts, commit=None):
    counts['files_checked'] += 1
    location = {'file': name}
    if commit:
        location['commit'] = commit
    # A path exemption permits reviewed runtime-shaped resources, never secrets.
    if name not in allowed and is_private_file(name):
        findings.append(dict(location, line=1, rule='private_runtime_file'))
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        counts['binary_files_not_text_scanned'] += 1
        return
    for rule, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            if rule == 'authenticated_url':
                url = urlsplit(match.group())
                reserved_host = url.hostname in {'example.com', 'example.org', 'example.net'} or (url.hostname or '').endswith(('.test', '.invalid', '.example'))
                explicit_dummy = url.username in {'owner', 'user', 'example', 'fixture'} and url.password in {'password', 'fixture-only', 'example', 'not-a-secret'}
                if reserved_host and explicit_dummy:
                    continue
            findings.append(dict(location, line=text.count('\n', 0, match.start())+1, rule=rule))
    lower = text.lower()
    for marker in markers:
        index = lower.find(marker.lower())
        if index >= 0:
            findings.append(dict(location, line=text.count('\n', 0, index)+1, rule='known_private_identifier'))

def check(args):
    root = Path(args.root).resolve() if args.root else None
    markers = []
    if args.markers_file:
        marker_path = Path(args.markers_file).resolve()
        if root and marker_path.is_relative_to(root):
            raise ValueError('Private markers must be stored outside the project')
        markers = [line.strip() for line in marker_path.read_text(encoding='utf-8-sig').splitlines() if line.strip() and not line.startswith('#')]
    findings = []
    counts = {'files_checked': 0, 'commits_checked': 0, 'binary_files_not_text_scanned': 0}
    allowed = set(args.allow_path)
    if args.artifact:
        with zipfile.ZipFile(args.artifact) as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                name = entry.filename
                if '..' in PurePosixPath(name).parts or name.startswith('/'):
                    findings.append({'file': name, 'line': 1, 'rule': 'unsafe_archive_path'})
                if '.git' in PurePosixPath(name).parts:
                    findings.append({'file': name, 'line': 1, 'rule': 'git_directory_in_archive'})
                if entry.file_size > 64*1024*1024:
                    findings.append({'file': name, 'line': 1, 'rule': 'oversized_file_requires_review'})
                    continue
                inspect(name, archive.read(entry), markers, allowed, findings, counts)
    elif args.history:
        seen = set()
        commits = git(root, 'rev-list', '--all').decode().splitlines()
        counts['commits_checked'] = len(commits)
        for commit in commits:
            for row in git(root, 'ls-tree', '-rz', commit).split(b'\0'):
                if not row:
                    continue
                metadata, name_raw = row.split(b'\t', 1)
                mode, kind, oid = metadata.decode().split()
                name = name_raw.decode('utf-8')
                if kind != 'blob':
                    findings.append({'file': name, 'commit': commit, 'line': 1, 'rule': 'external_git_object_requires_review'})
                    continue
                key = (oid, name)
                if key in seen:
                    continue
                seen.add(key)
                inspect(name, git(root, 'cat-file', 'blob', oid), markers, allowed, findings, counts, commit)
    else:
        names = git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z').split(b'\0')
        for name_raw in names:
            if not name_raw:
                continue
            name = name_raw.decode('utf-8')
            path = root/name
            if path.is_symlink():
                findings.append({'file': name, 'line': 1, 'rule': 'symlink_requires_review'})
            elif path.is_file():
                inspect(name, path.read_bytes(), markers, allowed, findings, counts)
    return dict(counts, passed=not findings, findings=findings,
                coverage='git-reachable-history' if args.history else 'zip' if args.artifact else 'git-worktree',
                limitations=['Binary visuals and metadata need separate review', 'Runtime configuration and remote retained copies need separate verification'])

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', help='Git project root')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--history', action='store_true')
    modes.add_argument('--worktree', action='store_true')
    modes.add_argument('--artifact', help='Source ZIP to inspect without extracting')
    parser.add_argument('--markers-file', help='Private newline-separated values outside the repository')
    parser.add_argument('--allow-path', action='append', default=[], help='Reviewed path exemption for runtime filenames only')
    args = parser.parse_args()
    if not args.artifact and not args.root:
        parser.error('--root is required for Git checks')
    try:
        result = check(args)
    except (ValueError, OSError, zipfile.BadZipFile):
        print(json.dumps({'passed': False, 'error': 'Unable to complete the check; verify paths, Git repository and ZIP format'}))
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['passed'] else 1

if __name__ == '__main__':
    sys.exit(main())
