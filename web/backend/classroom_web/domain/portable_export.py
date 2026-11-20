"""Build a bounded, inert aggregate ZIP from an already authorized snapshot."""
from dataclasses import dataclass
import hashlib
import io
import json
import zipfile
from typing import BinaryIO, Callable

from .portable import (ASSET, PORTABLE_V21, inspect_candidate, match, require, safe_path,
                       session_references)


@dataclass(frozen=True)
class ArchiveAsset:
    path: str
    size: int
    sha256: str
    open_stream: Callable[[], BinaryIO]


@dataclass(frozen=True)
class ArchiveClassroom:
    session: dict
    media: tuple[dict, ...]
    assets: tuple[ArchiveAsset, ...]


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _entry(path, content):
    return ArchiveAsset(path, len(content), hashlib.sha256(content).hexdigest(), lambda: io.BytesIO(content))


def require_portable_metadata(value):
    forbidden = {'ai_usage', 'authorization', 'consent', 'credentials', 'device', 'key_material_b64',
                 'password', 'password_hash', 'session', 'settings', 'tasks', 'token'}
    pending = [(0, value)]
    nodes = 0
    while pending:
        depth, item = pending.pop()
        nodes += 1
        require(depth <= 28 and nodes <= 250_000, 'CAPACITY_EXCEEDED')
        if isinstance(item, dict):
            require(not forbidden.intersection(item))
            pending.extend((depth + 1, child) for child in item.values())
        elif isinstance(item, (list, tuple)):
            require(len(item) <= PORTABLE_V21.files, 'CAPACITY_EXCEEDED')
            pending.extend((depth + 1, child) for child in item)


def aggregate_entries(scope, classrooms):
    policy = PORTABLE_V21
    require(scope in ('account', 'course', 'class_session') and 0 < len(classrooms) <= policy.files
            and (scope != 'class_session' or len(classrooms) == 1))
    identities = []
    entries = []
    metadata_bytes = 0
    declared_pixels = 0
    for classroom in classrooms:
        require_portable_metadata(classroom.session)
        require_portable_metadata(classroom.media)
        references = session_references(classroom.session)
        identity = classroom.session['id']
        identities.append(identity)
        entries.append(_entry(f'{identity}/session.json', _json(classroom.session)))
        session = entries[-1]
        manifest = dict(format='classroom-assistant-package', schema_version=1, source_session_id=identity,
                        include_audio=False, media=classroom.media,
                        files=[dict(path='session.json', size=session.size, sha256=session.sha256)])
        declared_assets = {}
        for item in classroom.media:
            if item.get('availability') != 'available':
                continue
            ref = references.get(item.get('original_path'))
            require(ref is not None and ref.kind == 'image' and ref.dimensions is not None)
            path = item.get('archive_path')
            require(match(ASSET, path))
            if path not in declared_assets:
                pixels = ref.dimensions[0] * ref.dimensions[1]
                require(pixels <= policy.image_pixels, 'CAPACITY_EXCEEDED')
                declared_pixels += pixels
            declared_assets[path] = item.get('size'), ref.sha256
        require(declared_pixels <= policy.total_image_pixels, 'CAPACITY_EXCEEDED')
        require(len(entries) + len(classroom.assets) + 2 <= policy.files, 'CAPACITY_EXCEEDED')
        assets = set()
        for asset in classroom.assets:
            require(match(ASSET, asset.path) and asset.path.endswith(('.png', '.jpg'))
                    and asset.path[7:-4] == asset.sha256 and type(asset.size) is int
                    and 0 < asset.size <= policy.part_bytes
                    and declared_assets.get(asset.path) == (asset.size, asset.sha256)
                    and asset.path not in assets)
            assets.add(asset.path)
            manifest['files'].append(dict(path=asset.path, size=asset.size, sha256=asset.sha256))
            entries.append(ArchiveAsset(f'{identity}/{asset.path}', asset.size, asset.sha256, asset.open_stream))
        require(assets == declared_assets.keys())
        entries.append(_entry(f'{identity}/manifest.json', _json(manifest)))
        metadata_bytes += session.size + entries[-1].size
        require(metadata_bytes <= policy.total_metadata_bytes and max(session.size, entries[-1].size)
                <= policy.metadata_bytes, 'CAPACITY_EXCEEDED')
    entries.append(_entry('aggregate.json', _json(dict(format='classroom-assistant-aggregate',
                         schema_version=1, scope=scope, sessions=identities))))
    names = [safe_path(entry.path) for entry in entries]
    require(len(names) == len(set(names)))
    metadata = [entry.size for entry in entries if entry.path.endswith('.json')]
    expanded = sum(entry.size for entry in entries)
    # A seekable ZIP_STORED archive without extra fields has deterministic framing.
    compressed = expanded + 22 + sum(76 + 2 * len(name) for name in names)
    require(len(entries) <= policy.files and expanded <= policy.expanded_bytes
            and compressed <= policy.archive_bytes and sum(metadata) <= policy.total_metadata_bytes
            and all(length <= policy.metadata_bytes for length in metadata), 'CAPACITY_EXCEEDED')
    return entries, compressed


def write_aggregate(output, scope, classrooms, *, heartbeat):
    entries, expected_size = aggregate_entries(scope, classrooms)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for entry in entries:
            heartbeat()
            remaining = entry.size
            digest = hashlib.sha256()
            info = zipfile.ZipInfo(entry.path)
            info.file_size = entry.size
            with entry.open_stream() as source, archive.open(info, 'w') as sink:
                while remaining:
                    block = source.read(min(1_000_000, remaining))
                    require(0 < len(block) <= remaining)
                    digest.update(block)
                    sink.write(block)
                    remaining -= len(block)
                require(not source.read(1) and digest.hexdigest() == entry.sha256, 'SOURCE_CHANGED')
    length = output.tell()
    require(length == expected_size, 'CAPACITY_EXCEEDED')
    inspection = inspect_candidate(output, length, 'application/zip')
    require(inspection.scope == scope and len(inspection.sessions) == len(classrooms))
    output.seek(0)
    digest = hashlib.sha256()
    for block in iter(lambda: output.read(1_000_000), b''):
        digest.update(block)
    output.seek(0)
    return length, digest.hexdigest(), inspection
