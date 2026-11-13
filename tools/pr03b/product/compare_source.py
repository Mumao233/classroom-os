"""Compare exact executed frozen inputs with current source, without mutation."""
import hashlib,json,re,sys
from pathlib import Path
R=Path(__file__).resolve().parents[3];O=R/'outputs/pr03b/product-completion-20261110-01'
run=sys.argv[1];assert re.fullmatch('[0-9a-f]{32}',run)
manifest=json.loads((O/f'capacity-{run}/SOURCE-MANIFEST.json').read_text());groups={};changed=[];missing=[]
for name,expected in manifest.items():
    prefix,_,relative=name.partition('/')
    base=R/'web/backend' if prefix=='backend' else R/'work/pr09a/dist' if prefix=='dist' else R/'tools/pr03b/product' if prefix=='harness' else None
    current=base/relative if base is not None else R/'docs/web-alpha/architecture/contracts'/name
    groups[prefix]=groups.get(prefix,0)+1
    if not current.is_file():missing.append(name)
    elif hashlib.sha256(current.read_bytes()).hexdigest()!=expected:changed.append(name)
report=dict(run=run,compared_groups=groups,changed_files=changed,missing_files=missing,all_frozen_inputs_match=not changed and not missing,note='Includes backend tests, current generated production dist and actual executed harness; final delivery docs/analyzer/freeze/sanitizer are outside measured input manifest.')
(O/'CAPACITY-SOURCE-MATCH.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
raise SystemExit(0 if report['all_frozen_inputs_match'] else 1)
