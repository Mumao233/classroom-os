"""Read bounded schema-1 classroom packages without extracting or executing files."""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import stat
import struct
import wave
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import PurePosixPath

from PIL import Image

SHA = re.compile(r'[a-f0-9]{64}')
LEGACY_ID = re.compile(r'[a-f0-9]{32}')
RECORD_ID = re.compile(r'[A-Za-z0-9_-]{1,128}')
SLIDE = re.compile(r'slides/[a-f0-9]{32}\.(png|jpg)')
ASSET = re.compile(r'assets/[a-f0-9]{64}\.(png|jpg|wav)')
WINDOWS_RESERVED = re.compile(r'(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)', re.I)
IMAGE_MIMES = {'.png': 'image/png', '.jpg': 'image/jpeg'}
UNAVAILABLE = {'missing', 'not_included', 'deleted', 'invalid', 'corrupt'}
BATCH_CONTEXT_FIELDS = {'primary_source_ids', 'context_source_ids', 'context_warnings'}


class InvalidPackage(ValueError):
    def __init__(self, code='VALIDATION_FAILED'):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PackageLimits:
    # Byte and work limits for synthetic experiments. See TBD-06.
    archive_bytes: int = 8_000_000
    expanded_bytes: int = 16_000_000
    metadata_bytes: int = 2_000_000
    files: int = 256
    ratio: int = 100
    image_pixels: int = 4_000_000
    total_image_pixels: int = 12_000_000

    def __post_init__(self):
        maxima = (16_000_000, 32_000_000, 4_000_000, 512, 200, 8_000_000, 24_000_000)
        if any(type(value) is not int or not 1 <= value <= maximum
               for value, maximum in zip(vars(self).values(), maxima, strict=True)):
            raise ValueError('package limits outside experimental bounds')


@dataclass(frozen=True)
class PortableV21Policy:
    version: str = 'tbd06-v2'
    archive_bytes: int = 500_000_000
    expanded_bytes: int = 1_000_000_000
    metadata_bytes: int = 16_000_000
    total_metadata_bytes: int = 64_000_000
    part_bytes: int = 16_000_000
    files: int = 10_000
    path_depth: int = 32
    ratio: int = 100
    image_pixels: int = 50_000_000
    total_image_pixels: int = 4_000_000_000
    pool_bytes: int = 5_000_000_000
    slots: int = 4
    worker_lease: timedelta = timedelta(minutes=5)
    confirmation_lease: timedelta = timedelta(minutes=30)
    access_age: timedelta = timedelta(hours=24)
    ticket_age: timedelta = timedelta(seconds=60)


PORTABLE_V21 = PortableV21Policy()


@dataclass(frozen=True)
class MediaReference:
    kind: str
    path: str
    identity: str | int
    version: int | None
    sha256: str | None
    dimensions: tuple[int, int] | None = None


@dataclass(frozen=True)
class PackageInspection:
    session: dict
    archive_sha256: str
    media_bytes: int
    warnings: tuple[str, ...]
    unsupported_fields: tuple[str, ...]


def require(condition, code='VALIDATION_FAILED'):
    if not condition:
        raise InvalidPackage(code)


def match(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def canonical_hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf8')).hexdigest()


def strict_json(raw, maximum, *, max_items=4096, max_nodes=60_000):
    require(len(raw) <= maximum, 'CAPACITY_EXCEEDED')

    def object_members(pairs):
        obj = {}
        for key, value in pairs:
            require(key not in obj)
            obj[key] = value
        return obj

    def reject_constant(_):
        raise InvalidPackage()

    try:
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=object_members,
                           parse_constant=reject_constant)
    except InvalidPackage:
        raise
    except (UnicodeError, ValueError, RecursionError) as error:
        raise InvalidPackage() from error
    pending = [(0, value)]
    count = 0
    while pending:
        depth, item = pending.pop()
        count += 1
        require(depth <= 28 and count <= max_nodes, 'CAPACITY_EXCEEDED')
        if type(item) is dict:
            require(all(len(key) <= 128 and key.isprintable() for key in item))
            require(not any(key in item for key in ('required_fields', 'required_features', 'critical')),
                    'PORTABLE_VERSION_UNSUPPORTED')
            pending.extend((depth + 1, child) for child in item.values())
        elif type(item) is list:
            require(len(item) <= max_items, 'CAPACITY_EXCEEDED')
            pending.extend((depth + 1, child) for child in item)
        elif isinstance(item, str):
            require(len(item) <= 400_000, 'CAPACITY_EXCEEDED')
            require(not any(0xD800 <= ord(char) <= 0xDFFF or char == '\0' for char in item))
        elif type(item) is float:
            require(math.isfinite(item))
        elif type(item) is int:
            require(abs(item) <= 2**53 - 1)
    return value


def schema(record, *, required=(), version_optional=False):
    require(type(record) is dict and set(required) <= record.keys())
    version = record.get('schema_version', 1 if version_optional else None)
    require(type(version) is int and version == 1, 'PORTABLE_VERSION_UNSUPPORTED')


def records(value):
    require(type(value) is list and all(type(item) is dict for item in value))
    return value


def rows(value, pattern=RECORD_ID):
    output = records(value)
    identities = [item.get('id') for item in output]
    require(all(match(pattern, item) for item in identities) and len(set(identities)) == len(identities))
    return output


def timestamp(value):
    return type(value) in (int, float) and 0 <= value <= 604800


def interval(item, *, open_end=False):
    start, end = item.get('start'), item.get('end')
    require(timestamp(start) and ((open_end and end is None) or (timestamp(end) and end >= start)))


def segment(item):
    interval(item)
    require(isinstance(item.get('text'), str))
    require(type(item.get('version', 0)) is int and item.get('version', 0) >= 0)
    if 'content_hash' in item:
        require(item['content_hash'] == canonical_hash({k: v for k, v in item.items() if k != 'content_hash'}))


def slide(item):
    interval(item, open_end=True)
    path = item.get('path')
    require(match(r'P[0-9]{4,}', item.get('id')) and match(SLIDE, path) and match(SHA, item.get('sha256')))
    version = item.get('version', 1)
    require(type(version) is int and version >= 1 and item.get('mime_type') == IMAGE_MIMES[path[-4:]])
    dimensions = None
    if 'width' in item or 'height' in item:
        dimensions = (item.get('width'), item.get('height'))
        require(all(type(value) is int and value > 0 for value in dimensions))
    if 'content_hash' in item:
        require(item['content_hash'] == canonical_hash({k: v for k, v in item.items() if k != 'content_hash'}))
    return MediaReference('image', path, item['id'], version, item['sha256'], dimensions)


def session_references(session):
    schema(session, required=('id', 'title', 'created_at'), version_optional=True)
    require(match(LEGACY_ID, session['id']) and isinstance(session['title'], str)
            and 0 < len(session['title']) <= 200 and bool(session['title'].strip())
            and isinstance(session['created_at'], str))
    try:
        datetime.fromisoformat(session['created_at'].replace('Z', '+00:00'))
    except ValueError as error:
        raise InvalidPackage() from error

    media = {}
    slide_versions = {}

    def register(ref):
        previous = media.setdefault(ref.path, ref)
        require((previous.kind, previous.identity, previous.version, previous.sha256)
                == (ref.kind, ref.identity, ref.version, ref.sha256))
        if ref.kind == 'image':
            require(slide_versions.setdefault((ref.identity, ref.version), ref.path) == ref.path)
            if previous.dimensions and ref.dimensions:
                require(previous.dimensions == ref.dimensions)
            if not previous.dimensions and ref.dimensions:
                media[ref.path] = ref

    def evidence(value, *, batch=False):
        schema(value, required=('segments', 'slides', 'source_ids'), version_optional=True)
        if 'source_session_id' in value:
            require(match(LEGACY_ID, value['source_session_id']))
        segments = rows(value['segments'], r'[SR][0-9]{4,}')
        slides = rows(value['slides'], r'P[0-9]{4,}')
        for item in segments:
            segment(item)
        for item in slides:
            register(slide(item))
        identities = {item['id'] for item in segments + slides}
        cited = value['source_ids']
        require(type(cited) is list and all(isinstance(key, str) and key in identities for key in cited)
                and len(cited) == len(identities) == len(set(cited)))
        omitted_from_hash = {'snapshot_hash'}
        if batch and BATCH_CONTEXT_FIELDS & value.keys():
            # The desktop planner hashes the subset before adding these fields.
            # Validate their relationships separately. Card and run-level hashes
            # still cover the full payload as written by the desktop exporter.
            require(BATCH_CONTEXT_FIELDS <= value.keys())
            primary, context = value['primary_source_ids'], value['context_source_ids']
            require(type(primary) is list and bool(primary) and type(context) is list)
            assigned = primary + context
            require(all(isinstance(key, str) and key in identities for key in assigned)
                    and len(assigned) == len(identities) == len(set(assigned)))
            warnings = value['context_warnings']
            require(type(warnings) is list and all(isinstance(warning, str) for warning in warnings))
            omitted_from_hash |= BATCH_CONTEXT_FIELDS
        if 'snapshot_hash' in value:
            require(value['snapshot_hash'] == canonical_hash({
                k: v for k, v in value.items() if k not in omitted_from_hash
            }))
        return identities

    for item in rows(session.get('web_transcript', []), LEGACY_ID):
        require(set(item) == {'id', 'text', 'start_ms', 'end_ms', 'version', 'sha256'})
        text = item['text']
        require(isinstance(text, str) and 0 < len(text.encode('utf8')) <= 32768
                and type(item['version']) is int and item['version'] >= 1
                and hashlib.sha256(text.encode('utf8')).hexdigest() == item['sha256'])
        start, end = item['start_ms'], item['end_ms']
        require((start is None and end is None) or
                (type(start) is int and type(end) is int and 0 <= start < end <= 2**53-1))
    for item in rows(session.get('web_images', []), r'P[0-9]{4,}'):
        path, version = item.get('path'), item.get('version')
        require(set(item) == {'id', 'path', 'version', 'mime_type', 'sha256', 'width', 'height'}
                and match(SLIDE, path) and type(version) is int and version >= 1
                and item['mime_type'] == IMAGE_MIMES[path[-4:]] and match(SHA, item.get('sha256'))
                and all(type(item[field]) is int and item[field] > 0 for field in ('width', 'height')))
        register(MediaReference('image', path, item['id'], version, item['sha256'], (item['width'], item['height'])))
    inherited_notes = set()
    for item in rows(session.get('web_notes', []), LEGACY_ID):
        expected = {'id', 'title', 'body', 'version', 'evidence'}
        require(expected <= item.keys() and item.keys() <= expected | {'personal_note', 'legacy_review_key'}
                and isinstance(item['title'], str) and len(item['title']) <= 200
                and isinstance(item['body'], str) and len(item['body']) <= 120000
                and type(item['version']) is int and item['version'] >= 1
                and type(item['evidence']) is list)
        annotation = item.get('personal_note', '')
        require(isinstance(annotation, str) and len(annotation) <= 12000)
        prior = item.get('legacy_review_key')
        if prior is not None:
            require(match(r'(cards|notes):[A-Za-z0-9_-]{1,128}', prior) and prior not in inherited_notes)
            inherited_notes.add(prior)
        for source in records(item['evidence']):
            require(set(source) == {'source_id', 'revision_id', 'frozen_text', 'content_sha256'}
                    and match(LEGACY_ID, source['source_id']) and match(LEGACY_ID, source['revision_id'])
                    and isinstance(source['frozen_text'], str) and match(SHA, source['content_sha256']))

    segments = rows(session.get('segments', []), r'S[0-9]{4,}')
    identities = {item['id'] for item in segments}
    for item in segments:
        segment(item)
    for item in rows(session.get('groups', [])):
        cited = item.get('segment_ids', [])
        require(type(cited) is list and all(isinstance(key, str) and key in identities for key in cited))
    for item in rows(session.get('revisions', [])):
        for part in rows(item.get('segments', []), r'[SR][0-9]{4,}'):
            segment(part)
    for item in rows(session.get('slides', []), r'P[0-9]{4,}'):
        current = slide(item)
        register(current)
        versions = set()
        for historical in records(item.get('versions', [])):
            previous = slide(historical)
            require(previous.identity == current.identity
                    and previous.version < current.version and previous.version not in versions)
            versions.add(previous.version)
            register(previous)
    for item in records(session.get('audio', [])):
        seq = item.get('seq')
        path = item.get('path')
        require(type(seq) is int and seq >= 0 and path == f'audio/{seq}.wav'
                and path not in media and timestamp(item.get('start')) and timestamp(item.get('duration'))
                and .1 <= item['duration'] <= 20)
        register(MediaReference('audio', path, seq, None, None))
    for item in rows(session.get('questions', [])):
        require(isinstance(item.get('question'), str) and isinstance(item.get('answer', ''), str))
        if 'evidence' in item:
            evidence(item['evidence'])
        elif item.get('study'):
            raise InvalidPackage()

    if inherited_notes and 'review' not in session:
        raise InvalidPackage()
    if 'review' in session:
        review = session['review']
        schema(review)
        for item in rows(review.get('marks', [])):
            require(item.get('target_kind') in ('segment', 'slide')
                    and match(RECORD_ID, item.get('target_id')))
            require(item['target_id'] in evidence(item.get('evidence')))
        for item in rows(review.get('runs', [])):
            parent = item.get('evidence')
            evidence(parent)
            sources = {source['id']: source for source in parent['segments'] + parent['slides']}
            for batch in rows(item.get('batches')):
                subset = batch.get('evidence')
                evidence(subset, batch=True)
                require(subset.get('source_session_id') == parent.get('source_session_id')
                        and all(sources.get(source['id']) == source
                                for source in subset['segments'] + subset['slides']))
        for collection in ('cards', 'notes'):
            for item in rows(review.get(collection, [])):
                allowed = evidence(item.get('evidence'))
                content, draft = item.get('ai_content'), item.get('user_draft')
                require(type(content) is dict and type(draft) is dict
                        and type(draft.get('revision')) is int and draft['revision'] >= 0
                        and isinstance(draft.get('content'), str) and isinstance(draft.get('personal_note'), str))
                parts = [content] if collection == 'cards' else records(content.get('sections'))
                fields = ('title', 'front', 'back') if collection == 'cards' else ('section', 'title', 'body')
                for part in parts:
                    require(all(isinstance(part.get(field), str) for field in fields))
                    cited = part.get('source_ids', [])
                    require(type(cited) is list and all(isinstance(key, str) and key in allowed for key in cited))
        known_review_ids = {f'{collection}:{item["id"]}' for collection in ('cards','notes')
                            for item in review.get(collection, ())}
        require(inherited_notes <= known_review_ids)
        selected = review.get('selected_note_id')
        if selected is not None:
            require(match(RECORD_ID, selected)
                    and selected in {item['id'] for item in review.get('notes', [])})
    return media


def archive_entries(source, length, limits, *, directories=0):
    # Bound the actual directory bytes before ZipFile allocates ZipInfo objects.
    # A non-ZIP64 archive is sufficient within the experimental bounds.
    tail_start = max(0, length - 65_557)
    source.seek(tail_start)
    tail = source.read(length - tail_start)
    end_at = tail.rfind(b'PK\x05\x06')
    require(end_at >= 0 and end_at + 22 <= len(tail))
    _, disk, first_disk, disk_count, count, size, offset, comment = struct.unpack_from('<4s4H2IH', tail, end_at)
    require(disk == first_disk == 0 and disk_count == count and end_at + 22 + comment == len(tail))
    maximum = limits.files + directories
    require(0 < count <= maximum and size <= maximum * 1024, 'CAPACITY_EXCEEDED')
    require(offset + size == tail_start + end_at)
    return count


def safe_path(name):
    require(isinstance(name, str) and 0 < len(name) <= 240 and name.isascii())
    path = PurePosixPath(name)
    require(not path.is_absolute() and path.as_posix() == name)
    require(all(part not in ('.', '..') and not part.endswith(('.', ' '))
                and not WINDOWS_RESERVED.match(part)
                and all(32 <= ord(char) < 127 and char not in '\\:<>"|?*' for char in part)
                for part in path.parts))
    return name


def read_member(archive, entry, max_bytes):
    require(entry.file_size <= max_bytes, 'CAPACITY_EXCEEDED')
    with archive.open(entry) as source:
        value = source.read(max_bytes + 1)
    require(len(value) == entry.file_size)
    return value


def check_png_chunks(content):
    # Local slide encoding removes ancillary compressed metadata. Reject it
    # before Pillow can inflate it outside the archive's pixel budget.
    offset = 8
    require(content[:offset] == b'\x89PNG\r\n\x1a\n')
    for _ in range(4096):
        require(offset + 12 <= len(content))
        length, kind = struct.unpack_from('>I4s', content, offset)
        offset += length + 12
        require(offset <= len(content) and kind not in (b'zTXt', b'iTXt', b'iCCP', b'acTL', b'fcTL', b'fdAT'))
        if kind == b'IEND':
            require(length == 0 and offset == len(content))
            return
    raise InvalidPackage('CAPACITY_EXCEEDED')


def verify_asset(content, name, pixel_budget):
    try:
        if name.endswith('.wav'):
            with wave.open(io.BytesIO(content), 'rb') as audio:
                frames = audio.getnframes()
                require((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype())
                        == (1, 2, 16_000, 'NONE') and 1600 <= frames <= 320_000)
                require(len(audio.readframes(frames)) == 2 * frames)
            return None
        expected = 'PNG' if name.endswith('.png') else 'JPEG'
        if expected == 'PNG':
            check_png_chunks(content)
        with Image.open(io.BytesIO(content), formats=[expected]) as image:
            require(image.width * image.height <= pixel_budget, 'CAPACITY_EXCEEDED')
            require(getattr(image, 'n_frames', 1) == 1)
            size = image.size
            image.verify()
        with Image.open(io.BytesIO(content), formats=[expected]) as image:
            image.load()
        return size
    except (OSError, ValueError, SyntaxError, EOFError, wave.Error, Image.DecompressionBombError) as error:
        if isinstance(error, InvalidPackage):
            raise
        raise InvalidPackage() from error


def inspect_zip(raw, limits):
    return inspect_zip_stream(io.BytesIO(raw), len(raw), limits)


def inspect_zip_stream(source, length, limits, *, v21=False, on_room=None):
    require(0 < length <= limits.archive_bytes, 'CAPACITY_EXCEEDED')
    max_dirs = limits.files if v21 else 0
    count = archive_entries(source, length, limits, directories=max_dirs)
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        require(len(entries) == count)
        expanded = sum(item.file_size for item in entries)
        require(expanded <= limits.expanded_bytes, 'CAPACITY_EXCEEDED')
        if v21:
            require(expanded <= length * limits.ratio, 'CAPACITY_EXCEEDED')
        names = set()
        by_name = {}
        for entry in entries:
            directory = entry.is_dir()
            original = entry.orig_filename
            name = safe_path(original[:-1] if directory and v21 else original)
            mode = (entry.external_attr >> 16) & 0o170000
            require(original == entry.filename and name.casefold() not in names
                    and not entry.flag_bits & 1
                    and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED))
            if v21:
                require(len(PurePosixPath(name).parts) <= limits.path_depth, 'CAPACITY_EXCEEDED')
            names.add(name.casefold())
            if directory:
                require(v21 and mode in (0, stat.S_IFDIR) and entry.file_size == 0 and entry.compress_size == 0)
                continue
            require(mode in (0, stat.S_IFREG))
            if entry.compress_type == zipfile.ZIP_STORED:
                require(entry.file_size == entry.compress_size)
            require(entry.file_size <= max(entry.compress_size, 1) * limits.ratio, 'CAPACITY_EXCEEDED')
            by_name[name] = entry
        require(len(by_name) <= limits.files and count-len(by_name) <= max_dirs, 'CAPACITY_EXCEEDED')
        if v21:
            metadata = [entry.file_size for name, entry in by_name.items() if name.lower().endswith('.json')]
            require(all(size <= limits.metadata_bytes for size in metadata)
                    and sum(metadata) <= limits.total_metadata_bytes, 'CAPACITY_EXCEEDED')
        if v21 and 'aggregate.json' in by_name:
            return inspect_aggregate(archive, by_name, limits, on_room=on_room)
        return inspect_members(archive, by_name, limits, v21, limits.total_image_pixels, on_room=on_room)[:4]


def inspect_members(archive, by_name, limits, v21, pixel_budget, *, on_room=None):
    require({'manifest.json', 'session.json'} <= by_name.keys())
    bounds = dict(max_items=limits.files, max_nodes=250_000) if v21 else {}
    manifest = strict_json(read_member(archive, by_name.pop('manifest.json'), limits.metadata_bytes),
                           limits.metadata_bytes, **bounds)
    schema(manifest, required=('format', 'source_session_id', 'media', 'files'))
    require(manifest['format'] == 'classroom-assistant-package', 'PORTABLE_VERSION_UNSUPPORTED')
    declared = {}
    for entry in records(manifest['files']):
        name = safe_path(entry.get('path'))
        require(name not in declared and name in by_name and match(SHA, entry.get('sha256'))
                and type(entry.get('size')) is int and entry['size'] == by_name[name].file_size)
        declared[name] = entry['sha256']
    require(declared.keys() == by_name.keys())
    session = strict_json(read_member(archive, by_name['session.json'], limits.metadata_bytes),
                          limits.metadata_bytes, **bounds)
    references = session_references(session)
    require(manifest['source_session_id'] == session['id'])
    assets, omitted = {}, set()
    registered = set()
    for item in records(manifest['media']):
        path, state = item.get('original_path'), item.get('availability')
        require(isinstance(path, str) and path in references and path not in registered
                and isinstance(state, str) and state in UNAVAILABLE | {'available'})
        ref = references[path]
        require(type(item.get('id')) is type(ref.identity) and item['id'] == ref.identity
                and item.get('kind') == ref.kind)
        if ref.kind == 'image':
            require(type(item.get('version')) is int and item['version'] == ref.version
                    and item.get('sha256') == ref.sha256)
        registered.add(path)
        if state != 'available':
            require('archive_path' not in item and state not in ('invalid', 'corrupt'))
            omitted.add(path)
            continue
        asset, sha = item.get('archive_path'), item.get('sha256')
        require(match(ASSET, asset) and match(SHA, sha)
                and asset == f'assets/{sha}{PurePosixPath(path).suffix}'
                and declared.get(asset) == sha and type(item.get('size')) is int
                and by_name[asset].file_size == item['size'] > 0)
        assets.setdefault(asset, []).append(ref)

    require(registered == references.keys()
            and assets.keys() == {name for name in declared if name.startswith('assets/')})
    for name, sha in declared.items():
        entry = by_name[name]
        if v21 and name not in assets:
            digest = hashlib.sha256()
            with archive.open(entry) as member:
                for block in iter(lambda: member.read(1_048_576), b''):
                    digest.update(block)
            require(digest.hexdigest() == sha)
            continue
        content = read_member(archive, entry, limits.part_bytes if v21 else limits.expanded_bytes)
        require(hashlib.sha256(content).hexdigest() == sha)
        if name in assets:
            size = verify_asset(content, name, min(limits.image_pixels, pixel_budget))
            require(all(ref.dimensions in (None, size) for ref in assets[name]))
            if size:
                pixel_budget -= size[0] * size[1]
    media_bytes = sum(by_name[name].file_size for name, refs in assets.items() if refs[0].kind == 'image')
    audio_excluded = any(ref.kind == 'audio' for ref in references.values())
    if on_room is not None:
        on_room(session, manifest['media'], archive, by_name)
    return session, media_bytes, bool(omitted), audio_excluded, pixel_budget

UNRESTORED_FIELDS = frozenset(('ai_usage','consent','credentials','device','flush_requested',
    'owner_id','pending_ids','session','settings','tasks'))


def candidate_report(session, omitted, audio):
    return dict(id=session['id'], omitted_media=omitted, audio_references=audio,
                unsupported_fields=sorted(set(session) & UNRESTORED_FIELDS),
                transcript_segments=len(session.get('segments', ())) + len(session.get('web_transcript', ())))


def inspect_aggregate(archive, by_name, limits, *, on_room=None):
    index = strict_json(read_member(archive, by_name.pop('aggregate.json'), limits.metadata_bytes),
                        limits.metadata_bytes, max_items=limits.files)
    schema(index, required=('format', 'scope', 'sessions'))
    require(set(index) == {'schema_version', 'format', 'scope', 'sessions'})
    require(index['format'] == 'classroom-assistant-aggregate', 'PORTABLE_VERSION_UNSUPPORTED')
    scope, identities = index['scope'], index['sessions']
    require(scope in ('account', 'course', 'class_session') and type(identities) is list
            and 0 < len(identities) <= limits.files and all(match(LEGACY_ID, item) for item in identities)
            and len(set(identities)) == len(identities))
    require(scope != 'class_session' or len(identities) == 1)
    groups = {identity: {} for identity in identities}
    for name, entry in by_name.items():
        root, separator, relative = name.partition('/')
        require(separator and root in groups and (relative in ('manifest.json', 'session.json')
                or match(ASSET, relative) and relative.endswith(('.png', '.jpg'))))
        groups[root][relative] = entry
    total_media = 0
    pixel_budget = limits.total_image_pixels
    reports = []
    for identity, members in groups.items():
        def verify_room(session, media, source, entries):
            require(session['id'] == identity)
            if on_room is not None:
                on_room(session, media, source, entries)
        session, media, omitted, audio, pixel_budget = inspect_members(
            archive, members, limits, True, pixel_budget, on_room=verify_room)
        total_media += media
        reports.append(candidate_report(session, omitted, audio))
    return scope, tuple(reports), total_media


@dataclass(frozen=True)
class CandidateInspection:
    scope: str
    sessions: tuple[dict, ...]
    media_bytes: int


def inspect_candidate(source, length, mime, *, on_room=None):
    """The same bounded importer is used for Preview and after ZIP generation."""
    policy = PORTABLE_V21
    try:
        source.seek(0)
        if mime == 'application/json':
            require(0 < length <= policy.metadata_bytes, 'CAPACITY_EXCEEDED')
            raw = source.read(length + 1)
            require(len(raw) == length)
            session = strict_json(raw, policy.metadata_bytes, max_items=policy.files, max_nodes=250_000)
            refs = session_references(session)
            if on_room is not None:
                media = [dict(kind=ref.kind, id=ref.identity, version=ref.version, sha256=ref.sha256,
                              original_path=ref.path, availability='not_included') for ref in refs.values()]
                on_room(session, media, None, {})
            audio = any(ref.kind == 'audio' for ref in refs.values())
            return CandidateInspection('class_session', (candidate_report(session, bool(refs), audio),), 0)
        require(mime == 'application/zip')
        result = inspect_zip_stream(source, length, policy, v21=True, on_room=on_room)
        if len(result) == 3:
            scope, reports, total_media = result
            return CandidateInspection(scope, reports, total_media)
        session, media, omitted, audio = result
        return CandidateInspection('class_session', (candidate_report(session, omitted, audio),), media)
    except (zipfile.BadZipFile, EOFError, RuntimeError, UnicodeError, struct.error, zlib.error) as error:
        raise InvalidPackage() from error


def inspect_package(raw: bytes, mime: str, limits: PackageLimits) -> PackageInspection:
    require(0 < len(raw) <= limits.archive_bytes, 'CAPACITY_EXCEEDED')
    warnings = []
    try:
        if mime == 'application/zip':
            session, media_bytes, omitted, audio_excluded = inspect_zip(raw, limits)
        elif mime == 'application/json':
            session = strict_json(raw, limits.metadata_bytes)
            refs = session_references(session)
            media_bytes, omitted = 0, bool(refs)
            audio_excluded = any(ref.kind == 'audio' for ref in refs.values())
        else:
            raise InvalidPackage()
    except (zipfile.BadZipFile, EOFError, RuntimeError, UnicodeError, struct.error, zlib.error) as error:
        raise InvalidPackage() from error
    if omitted:
        warnings.append('The package declares unavailable media or supplies only JSON metadata.')
    if audio_excluded:
        warnings.append('Original audio will not be restored.')
    ignored = tuple(sorted(set(session) & {
        'ai_usage', 'consent', 'credentials', 'device', 'flush_requested', 'owner_id',
        'pending_ids', 'session', 'settings', 'tasks',
    }))
    return PackageInspection(session, hashlib.sha256(raw).hexdigest(), media_bytes, tuple(warnings), ignored)
