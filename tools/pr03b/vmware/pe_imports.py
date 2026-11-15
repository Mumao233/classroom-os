"""List DLL imports of a PE file (recursively inside its own folder) that cannot be found
next to it or in the Windows system directories. Diagnostic only; stdlib only."""
import os, struct, sys
from pathlib import Path


def imports(path: Path) -> list[str]:
    data = path.read_bytes()
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    sections = struct.unpack_from('<H', data, pe + 6)[0]
    optional_size = struct.unpack_from('<H', data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from('<H', data, optional)[0]
    directory = optional + (112 if magic == 0x20B else 96)
    table = optional + optional_size
    rows = [struct.unpack_from('<IIII', data, table + 40 * i + 8) for i in range(sections)]

    def offset(rva: int) -> int | None:
        for size, address, raw_size, raw in rows:
            if address <= rva < address + max(size, raw_size):
                return raw + rva - address
        return None

    names = []
    for index in (1, 13):  # import table, delay-import table
        rva = struct.unpack_from('<I', data, directory + 8 * index)[0]
        at = offset(rva) if rva else None
        step, name_at = (20, 12) if index == 1 else (32, 4)
        while at is not None:
            row = data[at:at + step]
            if len(row) < step or not any(row):
                break
            name = offset(struct.unpack_from('<I', row, name_at)[0])
            if name is None:
                break
            names.append(data[name:data.index(b'\0', name)].decode('ascii', 'replace'))
            at += step
    return names


def main() -> int:
    start = Path(sys.argv[1]).resolve()
    here = start.parent
    system = [Path(os.environ['WINDIR']) / 'System32']
    seen, queue, missing = set(), [start], {}
    while queue:
        current = queue.pop()
        for name in imports(current):
            key = name.lower()
            if key in seen or key.startswith(('api-ms-', 'ext-ms-')):
                continue
            seen.add(key)
            local = here / name
            if local.is_file():
                queue.append(local)
            elif not any((folder / name).is_file() for folder in system):
                missing.setdefault(name, current.name)
    for name, wanted_by in sorted(missing.items()):
        print(f'MISSING {name} (needed by {wanted_by})')
    print(f'checked={len(seen)} missing={len(missing)}')
    return 1 if missing else 0


if __name__ == '__main__':
    raise SystemExit(main())
