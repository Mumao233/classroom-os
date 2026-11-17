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
# The complete suite includes contract checks that need the pinned validation packages
# (automation/validation-requirements.txt). The wheels were prepared for PR-07b; they
# are copied read-only and their hashes travel with the snapshot.
wheels = {}
wheelhouse = Path(sys.argv[2]) if len(sys.argv) > 2 else None
if wheelhouse is not None:
    (target / 'validation-wheels').mkdir()
    for wheel in sorted(wheelhouse.glob('*.whl')):
        content = wheel.read_bytes()
        (target / 'validation-wheels' / wheel.name).write_bytes(content)
        wheels[wheel.name] = hashlib.sha256(content).hexdigest()
    if not wheels:
        raise SystemExit('validation wheelhouse is empty')
record = dict(validation_wheels_sha256=wheels, base_sha=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
              files_sha256=manifest, zip_sha256=hashlib.sha256((target / 'source.zip').read_bytes()).hexdigest())
(target / 'SOURCE.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf8')
print(json.dumps(dict(files=len(manifest), source_sha256=record['zip_sha256'])))
