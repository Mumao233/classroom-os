"""Synthetic schema-1 archives with legacy source IDs and immutable slide versions."""
import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import threading
from types import SimpleNamespace
import zipfile

from PIL import Image
import pytest

from classroom_web.domain.portable import InvalidPackage, PackageLimits, inspect_package

SESSION = '1' * 32


def raw(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def png(color):
    output = io.BytesIO()
    Image.new('RGB', (2, 3), color).save(output, 'PNG')
    return output.getvalue()


def golden():
    def slide(path_id, version, image):
        return dict(id='P0001', path='slides/' + path_id * 32 + '.png', start=0, end=4,
                    version=version, width=2, height=3, mime_type='image/png', sha256=sha(image))

    old_bytes, new_bytes = png('red'), png('blue')
    old, latest = slide('a', 1, old_bytes), slide('b', 2, new_bytes)
    old_segment = dict(id='S0001', text='原稿', version=0, start=0, end=3, machine_text='原稿')
    old_segment['content_hash'] = sha(raw(old_segment))
    frozen = dict(old, content_hash=sha(raw(old)))
    evidence = dict(schema_version=1, source_session_id=SESSION, segments=[old_segment],
                    slides=[frozen], source_ids=['S0001', 'P0001'])
    evidence['snapshot_hash'] = sha(raw(evidence))
    session = dict(id=SESSION, title='Synthetic compatibility', created_at='2024-02-29T09:30:00+08:00',
                   segments=[dict(id='S0001', text='个人修订', version=1, start=0, end=3,
                                  machine_text='原稿')],
                   groups=[dict(id='G0001', segment_ids=['S0001'])],
                   slides=[dict(latest, versions=[old])], pending_ids=['S0001'],
                   review=dict(schema_version=1, cards=[dict(
                       id='card-01', evidence=evidence,
                       ai_content=dict(title='Synthetic', front='问题', back='答案', source_ids=['P0001']),
                       user_draft=dict(revision=2, content='个人笔记', personal_note='Synthetic'))]))
    files = {'index.html': b'<script>/* inert synthetic fixture */</script>',
             'reader/portable-reader.js': b'/* synthetic reader */'}
    media = []
    for reference, content in ((old, old_bytes), (latest, new_bytes)):
        name = f"assets/{reference['sha256']}.png"
        files[name] = content
        media.append(dict(kind='image', id=reference['id'], version=reference['version'],
                          sha256=reference['sha256'], original_path=reference['path'],
                          archive_path=name, size=len(content), availability='available'))
    return session, files, media


def archive(session, files, media, *, manifest_changes=None, extra=None, compression=zipfile.ZIP_STORED):
    combined = {'session.json': raw(session), **files}
    manifest = dict(format='classroom-assistant-package', schema_version=1, source_session_id=SESSION,
                    media=media, files=[dict(path=path, size=len(value), sha256=sha(value))
                                        for path, value in combined.items()])
    manifest.update(manifest_changes or {})
    content = io.BytesIO()
    with zipfile.ZipFile(content, 'w', compression=compression) as output:
        for path, value in combined.items():
            output.writestr(path, value)
        output.writestr('manifest.json', raw(manifest))
        if extra:
            output.writestr(*extra)
    return content.getvalue()


def inspect_zip(data, limits=None):
    return inspect_package(data, 'application/zip', limits or PackageLimits())


def test_golden_zip_and_json_preserve_declared_sources_without_using_reader():
    session, files, media = golden()
    package = archive(session, files, media)
    result = inspect_zip(package)
    assert result.archive_sha256 == sha(package)
    assert result.media_bytes == sum(item['size'] for item in media)
    assert result.session['slides'][0]['versions'][0]['sha256'] == media[0]['sha256']
    assert result.session['segments'][0]['text'] == '个人修订'
    card = result.session['review']['cards'][0]
    assert card['user_draft']['revision'] == 2
    assert card['evidence']['segments'][0]['text'] == '原稿'
    assert card['evidence']['slides'][0]['version'] == 1
    assert card['ai_content']['source_ids'] == ['P0001']
    assert 'pending_ids' in result.unsupported_fields
    json_package = b'\xef\xbb\xbf' + raw(session)
    inspected_json = inspect_package(json_package, 'application/json', PackageLimits())
    assert inspected_json.session == result.session
    assert inspected_json.media_bytes == 0 and inspected_json.warnings


def test_real_legacy_writer_produces_compatible_preview(tmp_path, monkeypatch):
    # The cross-product check uses only the old serializer. It never constructs
    # Classroom, a provider, an ASR engine, or any configuration reader.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[4]))
    from portable_package import PortableStore

    session, files, media = golden()
    for row in media:
        image = tmp_path / SESSION / row['original_path']
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(files[row['archive_path']])
    room = SimpleNamespace(data=tmp_path, lock=threading.RLock(), courses=[],
                           get=lambda identity: session if identity == SESSION else None)
    store = PortableStore(room)
    preview = store.preview_export(SESSION)
    output = store.build_export(preview['preview_id'], request_id='synthetic-web-preview')
    exported = store.download_path(output['id']).read_bytes()
    result = inspect_zip(exported)
    assert result.session['created_at'] == session['created_at']
    assert result.session['review']['cards'][0]['evidence'] == session['review']['cards'][0]['evidence']
    assert result.media_bytes == sum(row['size'] for row in media)


@pytest.mark.parametrize('path', ['../outside.txt', '/tmp/file', 'C:/file', 'reader\\file',
                                 'reader/../file', 'reader//file', 'reader/NUL.txt', 'INDEX.HTML'])
def test_invalid_archive_paths_are_rejected(path):
    session, files, media = golden()
    files[path] = b'inert'
    with pytest.raises(InvalidPackage):
        inspect_zip(archive(session, files, media))


def test_symlink_and_duplicate_entries_are_rejected():
    session, files, media = golden()
    link = zipfile.ZipInfo('reader/link')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(InvalidPackage):
        inspect_zip(archive(session, files, media, extra=(link, b'other')))
    with pytest.warns(UserWarning, match='Duplicate name'):
        data = archive(session, files, media, extra=('session.json', b'{}'))
    with pytest.raises(InvalidPackage):
        inspect_zip(data)


@pytest.mark.parametrize('field,value', [('schema_version', 2), ('required_fields', ['future'])])
def test_unsupported_schema_extensions_rejected(field, value):
    session, files, media = golden()
    with pytest.raises(InvalidPackage, match='PORTABLE_VERSION_UNSUPPORTED'):
        inspect_zip(archive(session, files, media, manifest_changes={field: value}))
    session[field] = value
    with pytest.raises(InvalidPackage, match='PORTABLE_VERSION_UNSUPPORTED'):
        inspect_package(raw(session), 'application/json', PackageLimits())


@pytest.mark.parametrize('raw_data', [b'{"id":"same","id":"again"}', b'{"value":NaN}',
                                     b'{"value":1e999}', b'{"value":"\\uD800"}'])
def test_bad_json_rejected(raw_data):
    with pytest.raises(InvalidPackage):
        inspect_package(raw_data, 'application/json', PackageLimits())


def test_missing_hash_reference_or_inconsistent_version_are_rejected():
    session, files, media = golden()
    bad_manifest = {'files': [dict(path='session.json', size=len(raw(session)), sha256='0' * 64)]}
    with pytest.raises(InvalidPackage):
        inspect_zip(archive(session, {}, [], manifest_changes=bad_manifest))
    with pytest.raises(InvalidPackage):
        inspect_zip(archive(session, files, media[:-1]))
    media[0]['version'] += 1
    with pytest.raises(InvalidPackage):
        inspect_zip(archive(session, files, media))
    session['review']['cards'][0]['ai_content']['source_ids'] = ['S0099']
    with pytest.raises(InvalidPackage):
        inspect_package(raw(session), 'application/json', PackageLimits())


def test_malformed_image_and_declared_mime_mismatch_fail():
    session, files, media = golden()
    ref = session['slides'][0]
    item = media[1]
    del files[item['archive_path']]
    broken = b'\x89PNG\r\n\x1a\n' + b'inert invalid image'
    digest = sha(broken)
    name = f'assets/{digest}.png'
    files[name] = broken
    item.update(archive_path=name, sha256=digest, size=len(broken))
    ref['sha256'] = digest
    with pytest.raises(InvalidPackage):
        inspect_zip(archive(session, files, media))
    ref['mime_type'] = 'image/jpeg'
    with pytest.raises(InvalidPackage):
        inspect_package(raw(session), 'application/json', PackageLimits())


def test_expanded_ratio_file_count_pixel_and_archive_budgets():
    session, files, media = golden()
    package = archive(session, files, media)
    for limits in (PackageLimits(archive_bytes=len(package)-1), PackageLimits(files=2),
                   PackageLimits(expanded_bytes=100), PackageLimits(image_pixels=5),
                   PackageLimits(total_image_pixels=10)):
        with pytest.raises(InvalidPackage, match='CAPACITY_EXCEEDED'):
            inspect_zip(package, limits)
    files['notes.md'] = b'a' * 16_000
    data = archive(session, files, media, compression=zipfile.ZIP_DEFLATED)
    with pytest.raises(InvalidPackage, match='CAPACITY_EXCEEDED'):
        inspect_zip(data)


def test_central_directory_count_must_match_the_archive():
    session, files, media = golden()
    data = bytearray(archive(session, files, media))
    end = data.rfind(b'PK\x05\x06')
    struct.pack_into('<HH', data, end + 8, 1, 1)
    with pytest.raises(InvalidPackage):
        inspect_zip(bytes(data))
