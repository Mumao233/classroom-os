"""Create a hashed tracked-source snapshot plus explicit PR-04a files for VMware."""
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile

root=Path(__file__).resolve().parents[2]
work=root/'work/pr04a'
output=root/'outputs/pr04a'
output.mkdir(parents=True,exist_ok=True)
files=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=root,text=True).splitlines()
manifest={}
with zipfile.ZipFile(work/'source.zip','w',zipfile.ZIP_DEFLATED) as archive:
    for name in files:
        path=root/name
        if path.is_file():
            content=path.read_bytes()
            archive.writestr(name,content)
            manifest[name]=hashlib.sha256(content).hexdigest()
record=dict(base_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
            files_sha256=manifest,zip_sha256=hashlib.sha256((work/'source.zip').read_bytes()).hexdigest())
(output/'SOURCE.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf8')
with zipfile.ZipFile(work/'payload.zip','w',zipfile.ZIP_STORED) as archive:
    archive.write(work/'source.zip','source.zip')
    archive.write(output/'SOURCE.json','SOURCE.json')
    archive.write(work/'downloads/seaweedfs-4.53-windows-amd64.zip','seaweedfs.zip')
    for path in (work/'wheels').glob('*.whl'):
        archive.write(path,'wheels/'+path.name)
print(json.dumps(dict(files=len(manifest),source_sha256=record['zip_sha256'],payload_bytes=(work/'payload.zip').stat().st_size)))
