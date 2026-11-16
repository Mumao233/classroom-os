"""Bundle only test reports, service logs and source manifests inside the guest."""
from pathlib import Path
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parents[2]
work = root/'work/pr04a'
output = root/'outputs/pr04a'
paths = [*output.glob('*.json'), *output.glob('*.xml'),
         *root.joinpath('outputs/pr03a/third-round').glob('pr04a-*.log')]
for run in work.iterdir():
    if run.is_dir():
        paths.extend(run.glob('command-*.log'))
        if (run/'seaweed.log').is_file():
            paths.append(run/'seaweed.log')
        if (run/'payload/SOURCE.json').is_file():
            paths.append(run/'payload/SOURCE.json')
manifest = {}
bundle = output/'evidence.zip'
with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(set(paths)):
        name = path.relative_to(root).as_posix()
        content = path.read_bytes()
        archive.writestr(name,content)
        manifest[name] = hashlib.sha256(content).hexdigest()
    archive.writestr('EVIDENCE-MANIFEST.json',json.dumps(manifest,indent=2)+'\n')
print(json.dumps(dict(path=str(bundle),sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),files=len(manifest))))
