"""Package owned test evidence, failures included, after every run has stopped."""
import hashlib,json,shutil,subprocess,sys,zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
R=Path(__file__).resolve().parents[3];W=R/'work/pr03b/product-completion-20261110-01';O=R/'outputs/pr03b/product-completion-20261110-01'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
allowed=set(subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=R).decode().split('\0'))
toolchains=O/'toolchain-records';toolchains.mkdir(exist_ok=True)
for name in ('linux-downloads.json','linux-pg-deps.json','linux-pg-packages.json'):
    if (W/name).is_file():shutil.copy2(W/name,toolchains/name)
for prefix in ('pg17-linux-installed','linux-python312'):
    target=toolchains/prefix;target.mkdir(exist_ok=True)
    for f in (W/prefix/'conda-meta').glob('*.json'):shutil.copy2(f,target/f.name)
binaries={'native_postgres':R/'work/pr03b/toolchains/pg17.11-win64-53b5364a5a04e8ec/runtime/Library/bin/postgres.exe','linux_postgres':W/'pg17-linux-installed/bin/postgres','linux_python312':W/'linux-python312/bin/python3.12'}
fingerprints={name:dict(path=p.relative_to(R).as_posix(),sha256=sha(p)) for name,p in binaries.items() if p.is_file()}
(toolchains/'BINARY-FINGERPRINTS.json').write_text(json.dumps(fingerprints,indent=2)+'\n')
for n in ('01','02'):
    source=R/f'work/pr00/pr03b-product-20261110-{n}';target=O/f'windows-original-{n}';target.mkdir(exist_ok=True)
    for f in source.iterdir():
        if f.is_file() and f.suffix in ('.log','.json','.md','.xml'):shutil.copy2(f,target/f.name)
executed=[]
for folder in sorted(p for p in O.glob('capacity-*') if p.is_dir()):
    if not (folder/'FINAL.json').exists():raise RuntimeError('capacity run has not finished: '+folder.name)
    final=json.loads((folder/'FINAL.json').read_text())
    if not all(final.get('cgroups_empty',{}).values()) or final.get('ram_unmounted') is not True:raise RuntimeError('owned run cleanup must be resolved before evidence freeze')
    manifest=json.loads((folder/'SOURCE-MANIFEST.json').read_text());source=W/folder.name/'source'
    archive=O/('executed-source-'+final['run']+'.zip')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,expected in manifest.items():
            if name.startswith('backend/') and 'web/'+name not in allowed:raise RuntimeError('unexpected non-source backend file: '+name)
            f=source/name
            if sha(f)!=expected:raise RuntimeError('executed source drift: '+name)
            z.write(f,name)
        z.write(folder/'SOURCE-MANIFEST.json','SOURCE-MANIFEST.json')
    executed.append(dict(run=final['run'],archive=archive.name,sha256=sha(archive),mode=final['mode']))
(O/'EXECUTED-SOURCES.json').write_text(json.dumps(executed,indent=2)+'\n')
subprocess.run([sys.executable,'-I','-B',R/'tools/pr03b/product/sanitize_diagnostics.py'],check=True,cwd=R)
xmls=list(O.rglob('*.xml'))
for f in xmls:ET.parse(f)
(O/'JUNIT-INTEGRITY.json').write_text(json.dumps(dict(files=len(xmls),all_parse=True),indent=2)+'\n')
excluded={'EVIDENCE-MANIFEST.json','EVIDENCE.json','PR03b-evidence-20261110.zip','PR03b-source-20261110.zip'}
files=sorted(p for p in O.rglob('*') if p.is_file() and p.relative_to(O).as_posix() not in excluded)
manifest={p.relative_to(O).as_posix():sha(p) for p in files}
manifest_file=O/'EVIDENCE-MANIFEST.json';manifest_file.write_text(json.dumps(manifest,indent=2)+'\n')
archive=O/'PR03b-evidence-20261110.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for p in files:z.write(p,p.relative_to(O).as_posix())
    z.write(manifest_file,manifest_file.name)
result=dict(file=archive.name,sha256=sha(archive),evidence_files=len(files),manifest_sha256=sha(manifest_file),source_snapshot=json.loads((O/'SNAPSHOT.json').read_text()),executed_sources=len(executed),all_owned_runs_cleaned=True)
(O/'EVIDENCE.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
