"""Sanitize owned diagnostics only; preserve failures, counts and locations."""
import hashlib,json,re
from pathlib import Path
R=Path(__file__).resolve().parents[3];O=R/'outputs/pr03b/product-completion-20261110-01';W=R/'work/pr03b/product-completion-20261110-01'
patterns=[re.compile(r'\$argon2id\$[^\s\x22\x27<>]+'),re.compile(r'([\x22\x27])(?:[A-Za-z0-9_-]{86}|[A-Za-z0-9_-]{43}|[A-Z2-7]{32}|[0-9]{6})\1'),re.compile(r'(&quot;|&#x27;|&#39;)(?:[A-Za-z0-9_-]{86}|[A-Za-z0-9_-]{43}|[A-Z2-7]{32}|[0-9]{6})\1')]
uri_patterns=[re.compile(r'((?:[?&]|&amp;)secret=)[A-Z2-7]{16,64}',re.I),re.compile(r'((?:[#?&]|&amp;)(?:token|cap|reauth_token)=)[A-Za-z0-9_-]{32,256}',re.I)]
paths=[p for p in O.rglob('*') if p.suffix in ('.log','.xml')]+list(W.glob('pg-*/command-*.log'))
records=[];sha=lambda b:hashlib.sha256(b).hexdigest()
for p in paths:
    assert p.resolve().is_relative_to(O.resolve()) or p.resolve().is_relative_to(W.resolve())
    original=p.read_bytes();text=original.decode('utf8',errors='replace');counts=[]
    for i,pattern in enumerate(patterns):
        text,count=pattern.subn('<redacted PHC>' if i==0 else lambda m:m.group(1)+'<redacted opaque value>'+m.group(1),text);counts.append(count)
    uri_count=0
    for pattern in uri_patterns:
        text,count=pattern.subn(lambda m:m.group(1)+'[redacted credential]',text);uri_count+=count
    if not sum(counts)+uri_count:continue
    # XML must remain parseable after redaction.
    if p.suffix=='.xml':text=text.replace('<redacted PHC>','[redacted PHC]').replace('<redacted opaque value>','[redacted opaque value]')
    result=text.encode('utf8');p.write_bytes(result)
    records.append(dict(path=p.relative_to(R).as_posix(),before_sha256=sha(original),after_sha256=sha(result),PHC_redactions=counts[0],opaque_redactions=sum(counts[1:])+uri_count,URI_redactions=uri_count))
record=O/'DIAGNOSTIC-REDACTIONS.json'
previous=json.loads(record.read_text()) if record.exists() else []
record.write_text(json.dumps(previous+records,indent=2)+'\n')
print(json.dumps(dict(checked=len(paths),changed=len(records),redactions=sum(r['PHC_redactions']+r['opaque_redactions'] for r in records))))
