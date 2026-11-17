"""Add a read-only PR-05b frontend snapshot to a disposable PR-08a guest archive."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import zipfile

payload = Path(sys.argv[1]).resolve()
capture_root = Path(sys.argv[2]).resolve()
record = json.loads((payload / 'SOURCE.json').read_text(encoding='utf-8'))
source = payload / 'source.zip'
with zipfile.ZipFile(source) as archive:
    contents = {name: archive.read(name) for name in archive.namelist()}
capture_files = sorted((capture_root / 'web/frontend/src/capture').glob('*.ts'))
if not capture_files:
    raise SystemExit('No PR-05b capture source supplied')
capture_hashes = {}
for file in capture_files:
    name = file.relative_to(capture_root).as_posix()
    if name in contents:
        raise SystemExit('Capture files already present in the base snapshot')
    contents[name] = file.read_bytes()
    capture_hashes[name] = hashlib.sha256(contents[name]).hexdigest()
asset = 'static/pcm-worklet.js'
contents[asset] = (capture_root / asset).read_bytes()
capture_hashes[asset] = hashlib.sha256(contents[asset]).hexdigest()
check = 'web/frontend/src/capture/cache-compatibility.ts'
contents[check] = b"""// Disposable combined verification source. Not part of either shipped package.
import { TrustedCache } from '../cache/trusted-cache';
import type { RecordingCachePort } from './ports';
export const checkRecordingCachePort = (cache: TrustedCache): RecordingCachePort => cache;
"""
record['combined_capture'] = {
    'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=capture_root, text=True).strip(),
    'source_worktree': str(capture_root),
    'files_sha256': capture_hashes,
    'injected_typecheck': check,
    'network': 'synthetic CaptureTransport, native IndexedDB and WebCrypto',
}
with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED) as archive:
    for name, data in sorted(contents.items()):
        archive.writestr(name, data)
record['files_sha256'] = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(contents.items())}
record['zip_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
(payload / 'SOURCE.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'combined_source_sha256': record['zip_sha256'], 'capture_files_sha256': capture_hashes}))
