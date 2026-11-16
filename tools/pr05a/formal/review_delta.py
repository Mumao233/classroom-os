"""Compare the exact independent-review snapshot and emit only the new review delta."""

import difflib
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parents[3]
    baseline = root / 'work/pr05a/formal/review-baseline-20261115'
    review = json.loads((baseline / 'review-snapshot.json').read_text(encoding='utf-8-sig'))
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root).decode('utf-8').split('\0')
    current = {name: (root / name).read_bytes() for name in set(names) if name and (root / name).is_file()}
    changes = []
    diffs = []
    for name in sorted(set(review['files_sha256']) | set(current)):
        after = current.get(name, b'')
        after_hash = hashlib.sha256(after).hexdigest() if name in current else None
        before_hash = review['files_sha256'].get(name)
        if before_hash == after_hash:
            continue
        before = b''
        if before_hash is not None:
            saved = baseline / name
            if saved.exists():
                before = saved.read_bytes()
            else:
                original = subprocess.check_output(['git', 'show', review['head'] + ':' + name], cwd=root)
                variants = (original, original.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
                before = next((value for value in variants if hashlib.sha256(value).hexdigest() == before_hash), None)
                if before is None:
                    raise ValueError('reviewed original bytes unavailable: ' + name)
        left = before.decode('utf-8-sig').splitlines(keepends=True)
        right = after.decode('utf-8-sig').splitlines(keepends=True)
        ranges = [dict(before_start=i+1, before_count=j-i, after_start=k+1, after_count=l-k)
                  for kind, i, j, k, l in difflib.SequenceMatcher(a=left, b=right).get_opcodes() if kind != 'equal']
        changes.append(dict(file=name, kind='added' if before_hash is None else 'deleted' if name not in current else 'modified',
                            before_sha256=before_hash, after_sha256=after_hash, ranges=ranges))
        diffs.extend(difflib.unified_diff(left, right, fromfile='review/' + name, tofile='current/' + name))
    output = root / 'outputs/pr05a/formal/review-delta'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'delta.json').write_text(json.dumps(dict(review_snapshot=str(baseline / 'review-snapshot.json'),
        review_head=review['head'], scope='this delta and directly affected calls/contracts/DDL/targeted tests',
        changes=changes), ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'delta.patch').write_text(''.join(diffs), encoding='utf-8')
    print(json.dumps({'changed_files': len(changes), 'output': str(output)}))


if __name__ == '__main__':
    main()
