"""Freeze source only; baseline/index protection and auditable changed-file list."""
import hashlib,json,subprocess,zipfile
from pathlib import Path
R=Path(__file__).resolve().parents[3];O=R/'outputs/pr03b/product-completion-20261110-01'
base=json.loads((O/'BASELINE.json').read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def git(*args):return subprocess.check_output(['git',*args],cwd=R).decode('utf8').strip()
files=sorted(set(subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=R).decode('utf8').split('\0'))- {''})
excluded=[f for f in files if f.startswith('web/backend/v/')]
files=[f for f in files if f not in excluded];manifest={f:sha(R/f) for f in files if (R/f).is_file()}
changed={f:dict(before=s,after=manifest.get(f)) for f,s in base['files'].items() if manifest.get(f)!=s}
protected={f:s for f,s in base['files'].items() if not f.startswith(('web/backend/','web/frontend/')) or f.startswith('web/backend/migrations/versions/')}
violations=[f for f,s in protected.items() if sha(R/f)!=s]
index=Path(base['index']);index_bytes_unchanged=sha(index)==base['index_sha256']
assert not git('diff','--cached','--name-status'),'Unexpected staged change'
assert not git('ls-files','--unmerged'),'Unexpected unmerged entry'
assert not violations,violations
assert git('rev-parse','HEAD')==base['head'] and git('branch','--show-current')==base['branch']
(O/'FINAL-SOURCE-MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
audit=dict(head=base['head'],branch=base['branch'],index_bytes_unchanged=index_bytes_unchanged,index_before_sha256=base['index_sha256'],index_current_sha256=sha(index),index_staged_diff_against_HEAD='',unmerged_entries=[],index_note='Raw index digest differs from baseline; no raw baseline index was saved, so exact byte preservation or the cause of the difference cannot be proved. Current staged content equals unchanged HEAD.',baseline_file_count=len(base['files']),protected_file_count=len(protected),protected_unchanged=True,changed_baseline_files=changed,new_files=[f for f in manifest if f not in base['files']],excluded_accidental_cache=excluded,staged=False,committed=False,pushed=False)
(O/'PRESERVATION-AUDIT.json').write_text(json.dumps(audit,indent=2)+'\n')
archive=O/'PR03b-source-20261110.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for f in manifest:z.write(R/f,f)
    z.write(O/'FINAL-SOURCE-MANIFEST.json','FINAL-SOURCE-MANIFEST.json')
(O/'SNAPSHOT.json').write_text(json.dumps(dict(file=archive.name,sha256=sha(archive),source_files=len(manifest),manifest_sha256=sha(O/'FINAL-SOURCE-MANIFEST.json')),indent=2)+'\n')
print(json.dumps(dict(source_files=len(manifest),changed_baseline_files=len(changed),protected_unchanged=len(protected),new_files=len(audit['new_files']),archive_sha256=sha(archive))))
