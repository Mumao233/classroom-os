"""Check review-migration source synchronization, syntax and offline rendering."""
from pathlib import Path
import ast
import hashlib
import io
import json
import os
import re
import subprocess
import sys

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
BACKEND = ROOT / 'web/backend'
OUT = ROOT / 'outputs/pr02a/remediation'
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(BACKEND))
from classroom_web.infrastructure.schema import semesters
from classroom_web.domain.policies import SEMESTER_NAME_MAX_LENGTH, SEMESTER_ACADEMIC_YEAR_MAX_LENGTH

source = (BACKEND / 'classroom_web/infrastructure/ddl.py').read_text('utf-8')
function = re.search(r'CREATE FUNCTION classroom_web_guard_tombstone\(\).*?\$fn\$;', source, re.S).group()
function = function.replace('CREATE FUNCTION', 'CREATE OR REPLACE FUNCTION', 1)
sql = (BACKEND / 'migrations/versions/0002_pr02a_review.sql').read_text('utf-8')
assert function in sql
assert 'FOR UPDATE' in function and function.index('FOR UPDATE', function.index('course_material_version')) < function.index('FROM public.deletion_requests')
initial_files = ['web/backend/migrations/versions/0001_data_foundation.py', 'web/backend/migrations/versions/0001_data_foundation.sql']
initial = {}
for file in initial_files:
    original = subprocess.check_output(['git', 'show', '272d1175b1919f76905861c0490a5253a25148d3:' + file])
    actual = (ROOT / file).read_bytes().replace(b'\r\n', b'\n')
    assert actual == original
    initial[file] = hashlib.sha256(actual).hexdigest()
assert any(str(c.name) == 'ck_semesters_name_length' for c in semesters.constraints)
assert any(str(c.name) == 'ck_semesters_academic_year_length' for c in semesters.constraints)
assert (SEMESTER_NAME_MAX_LENGTH, SEMESTER_ACADEMIC_YEAR_MAX_LENGTH) == (100, 30)
files = [*BACKEND.rglob('*.py'), *(ROOT / 'tools/pr02a').glob('*.py')]
for file in files:
    ast.parse(file.read_text('utf-8'), filename=str(file))
output = io.StringIO()
config = Config(str(BACKEND / 'alembic.ini'), output_buffer=output)
script = ScriptDirectory.from_config(config)
assert script.get_heads() == ['0002_pr02a_review']
assert script.get_revision('0002_pr02a_review').down_revision == '0001_data_foundation'
command.upgrade(config, 'head', sql=True)
rendered = output.getvalue()
assert 'COMMIT;' in rendered and 'CREATE OR REPLACE FUNCTION classroom_web_guard_tombstone()' in rendered
assert 'ck_semesters_name_length' in rendered and 'ck_semesters_academic_year_length' in rendered
assert '{"schema_version":1,"enabled":false}' in rendered
(OUT / 'migration-offline-head.sql').write_text(rendered, encoding='utf-8')
findings = []
for file in files + list((BACKEND / 'migrations').rglob('*.sql')):
    text = file.read_text('utf-8')
    for label, pattern in [('private key', r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'), ('AWS key', r'AKIA[0-9A-Z]{16}'), ('long token', r'sk-[A-Za-z0-9_-]{32,}')]:
        if re.search(pattern, text):
            findings.append((str(file), label))
assert not findings
report = {'python_ast_files': len(files), 'migration_heads': script.get_heads(), 'initial_migrations_unchanged': initial,
          'current_ddl_equals_0002_function': True, 'semester_contract_limits': [100,30],
          'offline_head_rendered': True, 'limited_secret_pattern_findings': findings, 'passed': True}
(OUT / 'static-final.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
