"""Capture exact frontend commands without pnpm's automatic reinstall wrapper."""
import json,os,subprocess,sys,time
from pathlib import Path
R=Path(__file__).resolve().parents[3];W=R/'work/pr03b/product-completion-20261110-01';O=R/'outputs/pr03b/product-completion-20261110-01';F=R/'web/frontend'
assert len(sys.argv)==2 and sys.argv[1] in ('checks','browser')
env=dict(os.environ,TEMP=str(W/'tmp'),TMP=str(W/'tmp'))
commands=([['node','node_modules/@playwright/test/cli.js','test','--config',str(R/'tools/pr03b/product/baseline-browser.config.mts'),'--reporter=json']]
    if sys.argv[1]=='browser' else [['node','scripts/gen-contracts.mjs','--check'],['node','node_modules/typescript/bin/tsc','-b','--pretty','false'],
    ['node','node_modules/eslint/bin/eslint.js','.','--max-warnings','0'],['node','node_modules/vitest/vitest.mjs','run','--config','tests/vitest.config.ts'],
    ['node','node_modules/vite/bin/vite.js','build','--mode','production'],['node','scripts/verify-build.mjs']])
rows=[];mode=sys.argv[1];stamp=str(time.time_ns())
for i,cmd in enumerate(commands):
    start=time.monotonic();p=subprocess.run(cmd,cwd=F,env=env,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=300,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    path=O/f'frontend-{mode}-{stamp}-{i}.log';path.write_text(p.stdout+p.stderr,encoding='utf8')
    row=dict(command=cmd,exit_code=p.returncode,seconds=round(time.monotonic()-start,3),log=path.name);rows.append(row);print(json.dumps(row),flush=True)
    if p.returncode:print((p.stdout+p.stderr)[-2000:],flush=True)
(O/f'frontend-{mode}-{stamp}.json').write_text(json.dumps(rows,indent=2)+'\n',encoding='utf8')
raise SystemExit(0 if all(r['exit_code']==0 for r in rows) else 1)
