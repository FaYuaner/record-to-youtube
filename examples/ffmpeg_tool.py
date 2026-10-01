"""Minimal working tool-protocol example: transcode an original to H.264/AAC."""
import json
from pathlib import Path
import subprocess
import sys

request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
target = Path(request['output_directory']) / 'final.mp4'
subprocess.run(['ffmpeg', '-nostdin', '-y', '-i', request['input_path'], '-c:v', 'libx264',
                '-preset', 'fast', '-crf', '22', '-c:a', 'aac', '-movflags', '+faststart', str(target)], check=True)
Path(request['result_path']).write_text(json.dumps({'final_path': str(target), **request['metadata']}, ensure_ascii=False), encoding='utf-8')
