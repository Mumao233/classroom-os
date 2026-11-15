"""Read-only local addition/size/whitespace check; no Git mutations."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PREFIXES = ('web/backend/classroom_web/asr_sim/', 'web/backend/tests/asr_sim/',
            'tools/pr06a/', 'docs/web-alpha/pr06a/')

if __name__ == '__main__':
    tracked = subprocess.check_output(['git', 'diff', '--name-only', 'HEAD'], cwd=ROOT, text=True)
    assert not tracked.strip(), 'existing tracked changes present'
    names = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'],
                                    cwd=ROOT, text=True).splitlines()
    assert names and len(names) <= 30
    total = 0
    for name in names:
        assert name.startswith(PREFIXES), name
        path = ROOT / name
        assert path.is_file() and not path.is_symlink(), name
        data = path.read_bytes()
        total += len(data)
        assert all(line.rstrip(b' \t') == line for line in data.splitlines()), name
    assert total <= 500000
    print('New regular files:', len(names), 'total bytes:', total)
    print('Existing tracked diff empty; allowed paths and whitespace passed.')
