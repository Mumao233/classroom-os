"""Read-only contract inspection helper."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if __name__ == '__main__':
    schema = json.loads((ROOT / 'docs/web-alpha/architecture/contracts/events.schema.json').read_text(encoding='utf-8'))
    for key, value in schema['$defs'].items():
        if 'partial' in key.lower() or 'final' in key.lower():
            print(key, json.dumps(value, indent=2))
    lines = (ROOT / 'docs/web-alpha/architecture/04-CONTRACTS-FLOWS.md').read_text(encoding='utf-8').splitlines()
    for i, line in enumerate(lines):
        if any(word in line for word in ('PCM', 'sample', 'drain', 'stream_id')):
            print('\n'.join(lines[max(0, i-1):i+3]))
