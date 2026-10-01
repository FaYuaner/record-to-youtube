"""Run a user's editing Skill with their own Codex CLI and emit the tool result."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
    skill = Path(request.get('skill_path', ''))
    if not skill.is_file():
        raise SystemExit('Configure skill_path with your own editing SKILL.md')
    executable = shutil.which('codex')
    if not executable:
        raise SystemExit('Install and sign in to the Codex CLI first')
    prefix = [executable]
    if os.name == 'nt' and Path(executable).suffix.lower() == '.ps1':
        prefix = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', executable]
    elif os.name == 'nt' and Path(executable).suffix.lower() in ('.cmd', '.bat'):
        ps_script = Path(executable).with_suffix('.ps1')
        if not ps_script.exists():
            raise SystemExit('Use a native Codex executable or its PowerShell launcher')
        prefix = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ps_script)]
    schema = Path(__file__).with_name('editing-result.schema.json')
    prompt = '''Edit the supplied video using the user's Skill below. Use local media tools.
Write the finished video inside output_directory and return its absolute final_path.
Keep the input unchanged. Do not publish, upload, send messages, access credentials,
or modify anything outside output_directory. Preserve supplied title/description.
If editing cannot be completed, fail clearly; do not return the original as an edit.
Only derive new metadata from content you actually inspected.

Recording request:
''' + json.dumps(request, ensure_ascii=False) + '\n\nUser editing Skill:\n' + skill.read_text(encoding='utf-8-sig')
    result = subprocess.run(prefix + ['exec', '--sandbox', 'workspace-write', '--skip-git-repo-check',
                                      '-C', request['output_directory'], '--output-schema', str(schema),
                                      '--output-last-message', request['result_path'], '-'],
                            input=prompt, text=True, encoding='utf-8', shell=False,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
