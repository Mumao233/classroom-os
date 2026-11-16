"""Verify a prepared source snapshot before running any application tests."""
from pathlib import Path
import hashlib
import json
import sys

root = Path(__file__).resolve().parents[2]
manifest = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
for name, expected in manifest['files_sha256'].items():
    target = (root/name).resolve()
    if not target.is_relative_to(root) or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
        raise SystemExit('source snapshot mismatch')
print(json.dumps(dict(base=manifest['base_sha'],verified_files=len(manifest['files_sha256']),
                     source_zip_sha256=manifest['zip_sha256'])))
