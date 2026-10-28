"""PR-01 document/contract validation only. Does not import or run application code."""
from pathlib import Path
import argparse, hashlib, json, re, subprocess, sys
from datetime import datetime, timezone

parser=argparse.ArgumentParser()
parser.add_argument('--deps', type=Path, required=True, help='Isolated directory containing jsonschema/openapi-spec-validator')
parser.add_argument('--output', type=Path, default=Path('outputs/pr01/architecture-validation.json'))
parser.add_argument('--expected-head', default='f7731002d974ea29296090c366b68569fc69ba89', help='Explicit full PR-00 or authorized PR-01 commit SHA; never inferred from HEAD')
args=parser.parse_args()
root=Path(__file__).resolve().parents[2]
arch=root/'docs/web-alpha/architecture'
sys.path.insert(0,str(args.deps.resolve()))
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate
checks=[]
def run(name, fn):
    try:
        details=fn()
        checks.append({'name':name,'status':'passed','details':details})
    except Exception as exc:
        checks.append({'name':name,'status':'failed','error':str(exc)})
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def git(*a): return subprocess.check_output(['git',*a],cwd=root).decode('utf-8').strip()
def pointer(doc, ref):
    assert ref.startswith('#/'), f'Nonlocal ref requires explicit review: {ref}'
    for part in ref[2:].split('/'): doc=doc[part.replace('~1','/').replace('~0','~')]
    return doc
api=read(arch/'contracts/openapi.json'); events=read(arch/'contracts/events.schema.json')
def api_check():
    validate(api)
    ops=[v for p in api['paths'].values() for k,v in p.items() if k in ('get','post','put','patch','delete')]
    ids=[o['operationId'] for o in ops]
    assert len(ids)==len(set(ids))
    return {'operations':len(ops),'paths':len(api['paths']),'schemas':len(api['components']['schemas'])}
run('OpenAPI3.1 + unique operationId',api_check)
run('Events JSON Schema 2020-12',lambda: (Draft202012Validator.check_schema(events),{'event_types':len(events['oneOf'])})[1])
def refs():
    count=0
    for doc in (api,events):
        def walk(x):
            nonlocal count
            if isinstance(x,dict):
                if '$ref' in x: pointer(doc,x['$ref']); count+=1
                for v in x.values(): walk(v)
            elif isinstance(x,list):
                for v in x: walk(v)
        walk(doc)
    return count
run('All local JSON refs resolve',refs)
def examples():
    data=read(arch/'contracts/examples.json'); results=[]
    for case in data['cases']:
        doc=read(arch/'contracts'/case['schema_file'])
        schema={'$schema':'https://json-schema.org/draft/2020-12/schema','$ref':case['schema_pointer'],**{k:v for k,v in doc.items() if k in ('components','$defs')}}
        actual=Draft202012Validator(schema,format_checker=FormatChecker()).is_valid(case['value'])
        assert actual==case['expected_valid'], f"{case['name']}: expected {case['expected_valid']} got {actual}"
        results.append({'name':case['name'],'expected_valid':actual})
    return results
run('Synthetic positive and negative contract fixtures',examples)
def transitions():
    d=read(arch/'contracts/state-machines.json')
    for name,m in d['machines'].items():
        assert len(m['states'])==len(set(m['states']))
        assert set(m['states'])==set(api['components']['schemas']['FlowStates']['properties'][name]['enum']),f'{name}: state enum drift'
        for t in m['transitions']:
            starts=t['from'] if isinstance(t['from'],list) else [t['from']]
            assert all(s in m['states'] for s in starts) and t['to'] in m['states'],name
    for c in d['cases']:
        matches=[t for t in d['machines'][c['machine']]['transitions'] if c['from'] in (t['from'] if isinstance(t['from'],list) else [t['from']]) and t['to']==c['to'] and t['event']==c['event']]
        allowed=any(set(t['requires'])<=set(c['facts']) for t in matches)
        assert allowed==c['allowed'],c['name']
    return {'machines':len(d['machines']),'guard_cases':len(d['cases']),'scope':'contract guard design; runtime race/transaction behavior not tested'}
run('Five workflow transition guards and negative traces',transitions)
def model():
    d=read(arch/'contracts/domain-model.json'); tables={t['name']:t for t in d['tables']}
    assert len(tables)==len(d['tables'])
    fk_count=0
    for t in tables.values():
        cols={c['name']:c for c in t['columns']}; assert len(cols)==len(t['columns']),t['name']
        for c in cols.values(): assert c['type'] and isinstance(c['nullable'],bool)
        for key in [t['primary_key'],*t['unique_keys'],*t['indexes']]:
            assert all(k in cols for k in key),(t['name'],key)
        for f in t['foreign_keys']:
            target=tables[f['references']['table']]; rc=f['references']['columns']; lc=f['columns']
            assert len(lc)==len(rc) and all(c in cols for c in lc)
            target_cols={c['name']:c for c in target['columns']}
            assert all(c in target_cols for c in rc),(t['name'],f)
            assert rc in [target['primary_key'],*target['unique_keys']],(t['name'],'FK target not unique',f)
            assert all(cols[l]['type']==target_cols[r]['type'] for l,r in zip(lc,rc)),(t['name'],'FK type mismatch')
            if t['tenant_scope']=='owner' and target['tenant_scope']=='owner':
                assert 'owner_id' in lc and 'owner_id' in rc,(t['name'],'unscoped private FK')
            fk_count+=1
        if t['tenant_scope']=='owner':
            assert 'owner_id' in cols and ['owner_id','id'] in t['unique_keys']
            assert not cols['owner_id']['nullable'] and 'FORCE RLS' in t['row_security']
    return {'tables':len(tables),'columns':sum(len(t['columns']) for t in tables.values()),'foreign_keys':fk_count,'scope':'structural model only; SQL checks/triggers/RLS not executed'}
run('Domain model keys, FK targets/types and owner scope',model)
def graph():
    prs=read(arch/'contracts/pr-dependencies.json')['prs']; by={p['id']:p for p in prs}
    assert len(prs)==len(by)==28
    orders={}
    for mode,keys in [('hard',['depends_on']),('all',['depends_on','integration_depends_on','paid_depends_on'])]:
        pending={k:set(x for name in keys for x in p.get(name,[])) for k,p in by.items()}; order=[]
        assert all(d in by for deps in pending.values() for d in deps)
        while pending:
            ready=sorted(k for k,v in pending.items() if not v)
            assert ready,'dependency cycle'
            order.extend(ready)
            for k in ready: del pending[k]
            for deps in pending.values(): deps.difference_update(ready)
        orders[mode]=order
    for k in ['06b','07a']: assert '11a' in by[k]['depends_on']
    assert by['09a']['depends_on']==['01']
    return orders
run('28 PR DAG hard + integration + paid dependencies',graph)
def trace():
    source=(root/'docs/web-alpha/REQUIREMENTS_MATRIX.md').read_text(encoding='utf-8')
    ids=set(re.findall(r'^\| ((?:[A-Z]+(?:-[A-Z]+)*-\d{2}|A(?:[1-9]|10))) \|',source,re.M))
    rows=read(arch/'contracts/requirements-traceability.json')['requirements']
    assert len(rows)==len(ids)==118 and {r['requirement_id'] for r in rows}==ids
    assert all((arch/r['design']).is_file() for r in rows)
    return {'requirements':118,'original_matrix_modified':False}
run('118 requirement IDs fully mapped',trace)
def documents():
    files=[*arch.rglob('*.md'),root/'AGENTS.md',root/'CLAUDE.md']; links=0; diagrams=0
    for p in files:
        s=p.read_text(encoding='utf-8'); assert len(re.findall(r'^```',s,re.M))%2==0,p.name
        assert not any(l.rstrip()!=l for l in s.splitlines()),f'trailing whitespace {p}'
        diagrams+=s.count('```mermaid')
        for link in re.findall(r'\]\(([^)]+)\)',s):
            link=link.strip('<>').split('#')[0]
            if not link or re.match(r'[a-zA-Z][a-zA-Z0-9+.-]*:',link): continue
            target=(p.parent/link).resolve()
            assert target.exists(),f'broken link {p.name}: {link}'
            links+=1
    return {'markdown_files':len(files),'local_links':links,'mermaid_blocks':diagrams}
run('Markdown local links, fences and whitespace',documents)
def protected():
    base='f7731002d974ea29296090c366b68569fc69ba89'
    assert re.fullmatch(r'[0-9a-f]{40}',args.expected_head),'expected HEAD must be a full SHA'
    assert git('rev-parse','HEAD')==args.expected_head,'HEAD differs from explicit expected commit'
    if args.expected_head != base:
        assert git('show','-s','--format=%P',args.expected_head)==base,'PR-01 must be one non-merge commit directly on PR-00'
    assert git('branch','--show-current')=='feature/web-alpha-0.1.0'
    architecture_files='''README.md
01-SYSTEM.md
02-FRONTEND-AURORA.md
03-DATA-BACKEND.md
04-CONTRACTS-FLOWS.md
05-SECURITY-OFFLINE.md
06-COST-OPERATIONS.md
07-ADR-REGISTER.md
08-IMPLEMENTATION.md
09-CLAUDE-FRONTEND-HANDOFF.md
10-TRACEABILITY.md
PR01-ACCEPTANCE.md
PR01-CLOSEOUT.md
contracts/CHANGELOG.md
contracts/domain-model.json
contracts/events.schema.json
contracts/examples.json
contracts/openapi.json
contracts/pr-dependencies.json
contracts/requirements-traceability.json
contracts/state-machines.json
evidence/INDEPENDENT-REVIEW.md
evidence/architecture-validation.json
evidence/diagram-validation.json'''.splitlines()
    expected={'AGENTS.md','CLAUDE.md','tools/pr01/check_diagrams.mjs','tools/pr01/validate_architecture.py',*(f'docs/web-alpha/architecture/{name}' for name in architecture_files)}
    assert len(expected)==28
    assert all((root/name).is_file() for name in expected),'missing PR-01 deliverable'
    base_files=set(git('ls-tree','-r','--name-only',base).splitlines())
    assert expected & base_files == {'AGENTS.md'},'PR-01 path would overwrite PR-00 file'
    changes=set(git('diff',base,'--name-only').splitlines())
    untracked=set(git('ls-files','--others','--exclude-standard').splitlines())
    assert changes | untracked == expected,{'unexpected':sorted((changes|untracked)-expected),'missing':sorted(expected-(changes|untracked))}
    prior=subprocess.check_output(['git','show',f'{base}:AGENTS.md'],cwd=root).decode('utf-8').replace('\r\n','\n')
    current=(root/'AGENTS.md').read_text(encoding='utf-8').replace('\r\n','\n')
    assert current.startswith(prior),'original AGENTS text lost/rewritten'
    m=read(root/'docs/web-alpha/evidence/baseline-manifest.json')
    for name,h in m['tracked_files'].items():
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==h, f'legacy hash changed: {name}'
    source_dir=root.parent/'Classroom_OS_Web_Alpha_0.1.0_PRD_v1.1_Package'
    original_hashes=sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in source_dir.iterdir() if p.is_file())
    assert original_hashes==sorted(m['original_document_files'].values()),'original source document content set changed'
    pdf=root/'docs/web-alpha/Classroom_OS_Web_Alpha_0.1.0_PRD_v1.2.pdf'
    assert hashlib.sha256(pdf.read_bytes()).hexdigest().upper()=='DD7D970D54FB443969CE6BB210B0B18053BFECF972CF419EFA57EDB51B2FF5B8'
    assert not (root/'web').exists(),'PR-02/09 implementation directory unexpectedly exists'
    subprocess.check_call(['git','diff','--check',base],cwd=root)
    subprocess.check_call(['git','diff','--cached','--check'],cwd=root)
    return {'legacy_files_hash_unchanged':len(m['tracked_files']),'original_document_hashes_unchanged':len(original_hashes),'AGENTS_original_prefix_preserved':True,'PRD_sha256_unchanged':True,'HEAD':args.expected_head,'PR00_base':base,'PR01_files':sorted(expected),'business_or_PR00_changes':'none; AGENTS explicitly authorized append only'}
run('Legacy/PR00/PRD/AGENTS/scope protection',protected)
out=args.output.resolve(); allowed=(root/'outputs/pr01').resolve()
assert out.is_relative_to(allowed),'Output must remain in outputs/pr01'
out.parent.mkdir(parents=True,exist_ok=True)
report={'at_utc':datetime.now(timezone.utc).isoformat(),'python':sys.version.split()[0],'checks':checks,'passed':all(c['status']=='passed' for c in checks),'scope':'static architecture/contract validation; no runtime product/paid/browser/DB integration tests'}
out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'passed':report['passed'],'checks':[{k:v for k,v in c.items() if k!='details'} for c in checks],'output':str(out)},ensure_ascii=False,indent=2))
sys.exit(0 if report['passed'] else 1)
