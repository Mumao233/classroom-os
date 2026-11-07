"""Read-only repair inventory; final immutable-diff gate belongs to controller."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PREFIXES = ('web/backend/classroom_web/asr_sim/', 'web/backend/tests/asr_sim/',
            'tools/pr06a/', 'docs/web-alpha/pr06a/')


if __name__ == '__main__':
    def git_names(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT,
                                       text=True).splitlines()

    changed = git_names('diff', '--name-only', 'HEAD')
    added = git_names('ls-files', '--others', '--exclude-standard')
    assert all(name.startswith(PREFIXES) for name in changed + added)
    files = [path for prefix in PREFIXES for path in (ROOT / prefix).rglob('*')
             if path.is_file()]
    assert all(not path.is_symlink() for path in files)
    size = sum(path.stat().st_size for path in files)
    assert len(files) <= 30 and size <= 500000
    for path in files:
        assert all(line.rstrip(b' \t') == line
                   for line in path.read_bytes().splitlines()), path
    print('SIMULATION ONLY: allowed-directory files:', len(files), 'bytes:', size)
    print('Allowed tracked changes:', len(changed), 'untracked additions:', len(added))
