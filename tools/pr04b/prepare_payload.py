"""Create a hashed source snapshot of this worktree for the VMware guest."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import zipfile

root = Path(__file__).resolve().parents[2]
target = Path(sys.argv[1])
target.mkdir(parents=True, exist_ok=True)
files = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard'], cwd=root, text=True).splitlines()
manifest = {}
with zipfile.ZipFile(target / 'source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for name in sorted(files):
        path = root / name
        if path.is_file():
            content = path.read_bytes()
            archive.writestr(name, content)
            manifest[name] = hashlib.sha256(content).hexdigest()
record = dict(base_sha=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
              files_sha256=manifest, zip_sha256=hashlib.sha256((target / 'source.zip').read_bytes()).hexdigest())
(target / 'SOURCE.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf8')
print(json.dumps(dict(files=len(manifest), source_sha256=record['zip_sha256'])))
