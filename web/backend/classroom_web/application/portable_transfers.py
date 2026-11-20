"""Explicitly synthetic PR-08c candidate exercised before the HTTP contract freezes."""
from contextlib import contextmanager
import hashlib
import secrets
from tempfile import TemporaryFile
from uuid import UUID, uuid4

from .media import MediaRangeRejected, fingerprint, identifier, stored
from ..domain.auth import AuthRejected
from ..domain.data import OwnerContext, Resource
from ..domain.media import ObjectUnavailable, byte_range
from ..domain.portable import InvalidPackage, PORTABLE_V21, SHA, inspect_candidate, match
from ..domain.portable_export import aggregate_entries, write_aggregate
from ..infrastructure.auth_deadlines import remaining
from ..infrastructure.media_repository import PgMediaRepository
from ..infrastructure.portable_lifecycle import PortableLifecycle, ticket_deadline
from ..test_support import validate_test_database_target

BLOCK = 1_000_000


def operation_key(value):
    key = identifier(value)
    if key.version != 4:
        raise AuthRejected('VALIDATION_FAILED', 422)
    return key


class PortableTransfers:
    def __init__(self, media, deletion, spool_directory):
        validate_test_database_target(media.database.engine.url.render_as_string(hide_password=False))
        if not media.store.synthetic_loopback or deletion.store is not media.store:
            raise ValueError('portable candidate requires isolated database and shared synthetic storage')
        self.media = media
        self.deletion = deletion
        self.spool_directory = spool_directory

    @contextmanager
    def _transaction(self, ctx, *, write=False):
        if not self.deletion.ready:
            raise AuthRejected('SERVICE_DISABLED', 503)
        if isinstance(ctx, OwnerContext):
            with self.media.database.transaction(ctx) as tx:
                yield PortableLifecycle(tx), PgMediaRepository(tx, ctx.owner_id), None
                if not self.deletion.ready:
                    raise AuthRejected('SERVICE_DISABLED', 503)
        else:
            with self.media.transaction(ctx, write=write) as (repo, principal):
                yield PortableLifecycle(repo.tx), repo, principal
                if not self.deletion.ready:
                    raise AuthRejected('SERVICE_DISABLED', 503)

    def _spool(self):
        return TemporaryFile(dir=self.spool_directory, mode='w+b')

    @staticmethod
    def _inspect(source, length, mime):
        try:
            return inspect_candidate(source, length, mime)
        except InvalidPackage as error:
            code = 'INCOMPATIBLE_CLIENT' if error.code == 'PORTABLE_VERSION_UNSUPPORTED' else error.code
            raise AuthRejected(code, 413 if code == 'CAPACITY_EXCEEDED' else 422) from None

    def begin_upload(self, ctx, key, length, digest, mime):
        if not match(SHA, digest) or mime not in ('application/zip', 'application/json'):
            raise AuthRejected('VALIDATION_FAILED', 422)
        if mime == 'application/json' and type(length) is int and length > PORTABLE_V21.metadata_bytes:
            raise AuthRejected('CAPACITY_EXCEEDED', 413)
        with self._transaction(ctx, write=True) as (life, _, _):
            row = life.reserve('import', length, operation_key(key),
                               fingerprint(dict(length=length, sha256=digest, mime_type=mime)),
                               archive_sha256=digest, mime=mime)
            return life.transfer(row['id'])

    def lock_legacy_admission(self, repo):
        if not self.deletion.ready:
            raise AuthRejected('SERVICE_DISABLED', 503)
        PortableLifecycle(repo.tx).gate()

    def register_legacy_upload(self, repo, upload, asset):
        life = PortableLifecycle(repo.tx)
        return self._link_legacy(life, upload, asset, verified=False)

    def adapt_legacy(self, ctx, upload_id):
        # A verified package created before the candidate may have no asset TTL.
        with self._transaction(ctx, write=True) as (life, repo, _):
            life.gate()
            upload, asset = repo.upload(identifier(upload_id))
            if upload['state'] != 'verified' or asset['status'] != 'committed':
                raise AuthRejected('SOURCE_UNAVAILABLE', 409)
            return self._link_legacy(life, upload, asset, verified=True)

    @staticmethod
    def _link_legacy(life, upload, asset, *, verified):
        if (asset['kind'] != 'import_package' or asset['class_session_id'] is not None
                or asset['mime_type'] not in ('application/json','application/zip')
                or asset['byte_length'] > PORTABLE_V21.part_bytes):
            raise AuthRejected('VALIDATION_FAILED', 422)
        upload_id = upload['id']
        digest = fingerprint({'legacy_upload': str(upload_id), 'sha256': asset['sha256']})
        row = life.reserve('import', asset['byte_length'], upload_id, digest,
                           archive_sha256=asset['sha256'], mime=asset['mime_type'])
        current = life.transfer(row['id'])
        cutoff = min(d for d in (current['access_until'],upload['expires_at'],asset['expires_at'],
                                 asset['hard_expire_at']) if d)
        life.rows('''INSERT INTO portable_transfer_media(owner_id,transfer_id,asset_id,part_number)
            VALUES(:owner,:transfer,:asset,0) ON CONFLICT (transfer_id,part_number) DO NOTHING''',
            transfer=current['id'], asset=asset['id'])
        life.rows('''UPDATE media_assets SET expires_at=:until WHERE owner_id=:owner AND id=:id
            AND (expires_at IS NULL OR expires_at>:until)''', id=asset['id'],until=cutoff)
        life.rows('''UPDATE portable_transfers SET state=:state,access_until=:until
            WHERE owner_id=:owner AND id=:id''', id=current['id'],until=cutoff,
            state='verified' if verified else current['state'])
        return current['id']

    def upload_status(self, ctx, identity):
        with self._transaction(ctx) as (life, _, _):
            upload = life.transfer(identifier(identity))
            if upload['kind'] != 'import':
                raise AuthRejected('NOT_FOUND', 404)
            return upload, [part for part in life.parts(upload['id']) if part['status'] == 'verified']

    def authorize_part(self, ctx, identity, number, offset, length, digest):
        if (type(number) is not int or type(offset) is not int or type(length) is not int
                or number < 0 or offset != number * PORTABLE_V21.part_bytes
                or not 0 < length <= PORTABLE_V21.part_bytes or not match(SHA, digest)):
            raise AuthRejected('VALIDATION_FAILED', 422)
        with self._transaction(ctx, write=True) as (life, _, _):
            upload = life.transfer(identifier(identity))
            expected = min(PORTABLE_V21.part_bytes, upload['reserved_bytes'] - offset)
            if upload['kind'] != 'import' or length != expected:
                raise AuthRejected('VALIDATION_FAILED', 422)

    def put_part(self, ctx, key, identity, number, offset, length, digest, source):
        identity, key = identifier(identity), operation_key(key)
        signature = fingerprint(dict(upload=str(identity), number=number, offset=offset, length=length, sha256=digest))
        self.authorize_part(ctx, identity, number, offset, length, digest)
        with self._spool() as content:
            verify = hashlib.sha256()
            left = length
            while left:
                remaining()
                block = source.read(min(BLOCK, left))
                if not 0 < len(block) <= left:
                    raise AuthRejected('VALIDATION_FAILED', 422)
                content.write(block)
                verify.update(block)
                left -= len(block)
            if source.read(1) or verify.hexdigest() != digest:
                raise AuthRejected('VALIDATION_FAILED', 422)
            with self._transaction(ctx, write=True) as (life, repo, _):
                # Every path acquires the transfer gate before operation receipt locks.
                life.gate()
                prior = repo.receipt('portablePutPart', key, signature)
                part = life.stage(identity, number, length, digest, 'application/octet-stream')
                if prior:
                    return prior['response_body']
                if repo.receipt('portablePutIntent', key, signature) is None:
                    repo.remember('portablePutIntent', key, signature, 'media_asset', part['id'], {}, 202)
            obj = self.write_staged(ctx, identity, part, content)
        with self._transaction(ctx, write=True) as (life, repo, _):
            life.attach(identity, part, obj)
            response = dict(upload_id=str(identity), part_number=number, offset=offset, byte_length=length, sha256=digest)
            if repo.receipt('portablePutPart', key, signature) is None:
                repo.remember('portablePutPart', key, signature, 'media_asset', part['id'], response, 200)
            return response

    def write_staged(self, ctx, transfer_id, part, content, *, copy=False):
        # The eraser has to obtain the same row before proving absence. This
        # transaction holds no platform gate during the external operation.
        table = 'portable_import_assets' if copy else 'media_assets'
        with self._transaction(ctx) as (life, _, _):
            row = life.rows(f'SELECT id FROM {table} WHERE owner_id=:owner AND id=:id FOR SHARE',
                            id=part['id']).one_or_none()
            if row is None:
                raise AuthRejected('GONE', 410)
            life.transfer(transfer_id)
            return self._put(part, content)

    def _put(self, part, content):
        try:
            return self.media.store.put_portable(part['object_key'], content, part['byte_length'], part['sha256'])
        except ObjectUnavailable:
            raise AuthRejected('STORAGE_UNAVAILABLE', 503) from None

    def _assemble(self, ctx, upload, parts, output=None, *, pulse=None):
        digest = hashlib.sha256()
        length = 0
        for part in parts:
            if pulse is not None:
                pulse()
            with self._transaction(ctx) as (life, _, _):
                life.transfer(upload['id'])
            pinned = stored(part)
            check = hashlib.sha256()
            for offset in range(0, pinned.byte_length, BLOCK):
                try:
                    block = self.media.store.read(pinned, offset, min(offset + BLOCK, pinned.byte_length) - 1)
                except ObjectUnavailable:
                    raise AuthRejected('STORAGE_UNAVAILABLE', 503) from None
                check.update(block)
                digest.update(block)
                length += len(block)
                if output is not None:
                    output.write(block)
            if check.hexdigest() != part['sha256']:
                raise AuthRejected('SOURCE_CHANGED', 409)
        if length != upload['reserved_bytes'] or digest.hexdigest() != upload['archive_sha256']:
            raise AuthRejected('SOURCE_CHANGED', 409)

    def _complete_parts(self, upload, parts):
        length = upload['reserved_bytes']
        count = (length + PORTABLE_V21.part_bytes - 1) // PORTABLE_V21.part_bytes
        if ([p['part_number'] for p in parts] != list(range(count))
                or any(p['status'] not in ('verified', 'committed') for p in parts)):
            raise AuthRejected('SOURCE_UNAVAILABLE', 409)

    def seal(self, ctx, key, identity, digest):
        key, identity = operation_key(key), identifier(identity)
        signature = fingerprint(dict(upload=str(identity), sha256=digest))
        with self._transaction(ctx, write=True) as (life, repo, _):
            upload = life.transfer(identity)
            if upload['kind'] != 'import' or digest != upload['archive_sha256']:
                raise AuthRejected('VALIDATION_FAILED', 422)
            prior = repo.receipt('portableSeal', key, signature)
            if prior:
                return prior['response_body']
            parts = life.parts(identity)
            self._complete_parts(upload, parts)
        # A seal has no permanent assembled duplicate in the bucket.
        try:
            self._assemble(ctx, upload, parts)
        except AuthRejected as error:
            if error.code == 'SOURCE_CHANGED':
                self.cancel(ctx, identity)
            raise
        with self._transaction(ctx, write=True) as (life, repo, _):
            life.mark_verified(identity)
            result = dict(upload_id=str(identity), state='verified')
            if repo.receipt('portableSeal', key, signature) is None:
                repo.remember('portableSeal', key, signature, 'media_asset', parts[0]['id'], result, 200)
            return result

    def preview(self, ctx, key, identity, course_id):
        key, identity, course_id = operation_key(key), identifier(identity), identifier(course_id)
        signature = fingerprint(dict(upload=str(identity), course=str(course_id)))
        worker = uuid4()
        with self._transaction(ctx, write=True) as (life, repo, _):
            repo.tx.catalog._load(Resource.COURSE, course_id, share=True)
            upload = life.transfer(identity, state='verified')
            slot = life.claim(identity, key, signature, worker, destination=course_id)
            if slot['state'] == 'waiting' and slot['deadline'] > life.now():
                return repo.receipt('portablePreviewV21', key, signature)['response_body']
            if slot['worker_id'] != worker:
                raise AuthRejected('GONE' if slot['finished_at'] else 'SOURCE_UNAVAILABLE',
                                   410 if slot['finished_at'] else 409)
            parts = life.parts(identity)
        lease = slot['id'], worker, slot['fence']
        try:
            with self._spool() as output:
                self._assemble(ctx, upload, parts, output, pulse=lambda: self._heartbeat(ctx, lease))
                inspection = self._inspect(output, upload['reserved_bytes'], upload['mime_type'])
            with self._transaction(ctx, write=True) as (life, repo, _):
                repo.tx.catalog._load(Resource.COURSE, course_id, share=True)
                waiting = life.await_confirmation(*lease)
                warnings = []
                if any(room['omitted_media'] for room in inspection.sessions):
                    warnings.append('Some package media could not be restored from the supplied bytes.')
                if any(room['audio_references'] for room in inspection.sessions):
                    warnings.append('Original audio will not be restored.')
                discarded = sorted({field for room in inspection.sessions for field in room['unsupported_fields']})
                result = dict(warnings=warnings, unsupported_fields=discarded,
                              id=str(key), slot_id=str(slot['id']), class_session_count=len(inspection.sessions),
                              media_bytes=inspection.media_bytes, scope=inspection.scope,
                              archive_sha256=upload['archive_sha256'], expires_at=waiting['deadline'].isoformat())
                repo.remember('portablePreviewV21', key, signature, 'media_asset', parts[0]['id'], result, 201)
                return result
        except (AuthRejected, OSError):
            self._fail(ctx, lease)
            raise

    def _heartbeat(self, ctx, lease):
        with self._transaction(ctx) as (life, _, _):
            life.heartbeat(*lease)

    def _fail(self, ctx, lease):
        try:
            with self._transaction(ctx) as (life, _, _):
                life.finish(*lease, 'failed')
        except AuthRejected:
            # Revocation or a reclaimed lease already owns cleanup.
            pass

    def cancel(self, ctx, identity, *, key=None, import_only=False):
        requested = identifier(identity)
        with self._transaction(ctx, write=True) as (life, repo, _):
            life.gate()
            row = life.rows('''SELECT id,kind FROM portable_transfers WHERE owner_id=:owner AND id=:id''',
                            id=requested).one_or_none()
            if row is None:
                row = life.rows('''SELECT t.id,t.kind FROM media_uploads u
                    JOIN portable_transfer_media p ON (p.owner_id,p.asset_id)=(u.owner_id,u.asset_id)
                    JOIN portable_transfers t ON (t.owner_id,t.id)=(p.owner_id,p.transfer_id)
                    WHERE u.owner_id=:owner AND u.id=:upload AND t.kind='import' ''',
                    upload=requested).one_or_none()
            if row is None or (import_only and row['kind'] != 'import'):
                raise AuthRejected('NOT_FOUND', 404)
            transfer_id = row['id']
            digest = fingerprint({'transfer_id': str(transfer_id)})
            op = operation_key(key) if key is not None else None
            prior = repo.receipt('portableCancel', op, digest) if op is not None else None
            closed = life.close(transfer_id)
            if op is not None and prior is None:
                repo.remember('portableCancel', op, digest, 'media_upload', transfer_id, {}, 200)
            life.gate()
            return closed

    @staticmethod
    def _authorize_sources(repo, sources):
        for source in sorted(sources):
            repo.tx.catalog._load(Resource.CLASS_SESSION, source, share=True)

    @staticmethod
    def authorize_dependencies(life, repo, transfer_id):
        deps = life.rows('''SELECT kind,source_id FROM portable_export_dependencies
            WHERE owner_id=:owner AND transfer_id=:id''', id=transfer_id).all()
        assets = [row['source_id'] for row in deps if row['kind']=='media']
        sources = [row['source_id'] for row in deps if row['kind']=='transcript']
        if assets:
            visible = life.rows('''SELECT id FROM media_assets WHERE owner_id=:owner AND id=ANY(:ids)
                AND status='committed' AND deleted_at IS NULL AND access_revoked_at IS NULL
                AND (expires_at IS NULL OR expires_at>clock_timestamp())
                AND (hard_expire_at IS NULL OR hard_expire_at>clock_timestamp()) ORDER BY id FOR SHARE''',ids=assets).all()
            if len(visible) != len(assets):
                raise AuthRejected('GONE', 410)
        if sources:
            visible = life.rows('''SELECT id FROM qa_sources WHERE owner_id=:owner AND id=ANY(:ids)
                ORDER BY id FOR SHARE''', ids=sources).all()
            if len(visible) != len(sources):
                raise AuthRejected('GONE', 410)

    def publish_zip(self, ctx, key, scope, classrooms, *, include_audio=False):
        """Worker-side candidate publication from already supplied immutable snapshots."""
        if include_audio:
            raise AuthRejected('VALIDATION_FAILED', 422)
        try:
            _, expected_length = aggregate_entries(scope, classrooms)
        except InvalidPackage as error:
            raise AuthRejected(error.code, 413 if error.code == 'CAPACITY_EXCEEDED' else 422) from None
        sources = [UUID(hex=row.session['id']) for row in classrooms]
        request_hash = fingerprint(dict(scope=scope, snapshots=[
            dict(session=row.session, media=row.media) for row in classrooms]))
        op = operation_key(key)
        worker = uuid4()
        with self._transaction(ctx, write=True) as (life, repo, _):
            self._authorize_sources(repo, sources)
            transfer = life.reserve('export', expected_length, op, request_hash)
            slot = life.claim(transfer['id'], op, request_hash, worker)
            if slot['state'] == 'completed' and transfer['state'] == 'published':
                return self._export_metadata(life.parts(transfer['id'])[0])
            if slot['worker_id'] != worker:
                raise AuthRejected('SOURCE_UNAVAILABLE', 409)
            life.bind_sources(transfer['id'], sources)
        lease = slot['id'], worker, slot['fence']
        try:
            with self._spool() as output:
                length, digest, _ = write_aggregate(output, scope, classrooms, heartbeat=lambda: self._heartbeat(ctx, lease))
                with self._transaction(ctx) as (life, repo, _):
                    self._authorize_sources(repo, sources)
                    life.gate()
                    life.live_work(*lease)
                    asset = life.stage(transfer['id'], 0, length, digest, 'application/zip')
                obj = self.write_staged(ctx, transfer['id'], asset, output)
            with self._transaction(ctx) as (life, repo, _):
                self._authorize_sources(repo, sources)
                life.attach(transfer['id'], asset, obj)
                life.finish(*lease, 'completed', inspected_sha256=digest)
                return self._export_metadata(life.parts(transfer['id'])[0])
        except InvalidPackage as error:
            self._fail(ctx, lease)
            raise AuthRejected(error.code, 413 if error.code == 'CAPACITY_EXCEEDED' else 422) from None
        except (AuthRejected, OSError):
            self._fail(ctx, lease)
            raise

    @staticmethod
    def _export_metadata(asset):
        version = fingerprint(dict(id=str(asset['id']), etag=asset['object_etag'],
                                   version=asset['object_version'], revision=asset['revision']))
        return dict(asset_id=str(asset['id']), asset_version=version, sha256=asset['sha256'],
                    byte_length=asset['byte_length'])

    def _export_asset(self, life, repo, asset_id):
        linked = life.rows('''SELECT p.transfer_id FROM portable_transfer_media p
            JOIN portable_transfers t ON (t.owner_id,t.id)=(p.owner_id,p.transfer_id)
            WHERE p.owner_id=:owner AND p.asset_id=:asset AND t.kind='export' ''', asset=asset_id).one_or_none()
        if linked is None:
            raise AuthRejected('NOT_FOUND', 404)
        transfer = life.transfer(linked['transfer_id'], state='published')
        sources = life.sources(transfer['id'])
        if not sources:
            raise AuthRejected('GONE', 410)
        self._authorize_sources(repo, sources)
        self.authorize_dependencies(life, repo, transfer['id'])
        # The generic MediaService will not issue access to this verified-only asset.
        asset = repo.asset(asset_id)
        if asset['status'] != 'verified':
            raise AuthRejected('GONE', 410)
        pinned = stored(asset)
        try:
            current = self.media.store.inspect(pinned.key)
        except ObjectUnavailable:
            raise AuthRejected('STORAGE_UNAVAILABLE', 503) from None
        if current is None or (current.etag, current.version_id, current.byte_length) != (
                pinned.etag, pinned.version_id, pinned.byte_length):
            raise AuthRejected('SOURCE_CHANGED', 409)
        return asset, pinned, transfer

    def issue_ticket(self, ctx, asset_id, *, expected_version=None, key=None):
        with self._transaction(ctx, write=True) as (life, repo, principal):
            repo.execute("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:owner AS text)||'media_ticket',0))")
            asset, _, transfer = self._export_asset(life, repo, identifier(asset_id))
            metadata = self._export_metadata(asset)
            deadline = ticket_deadline(life.now(), principal.expires_at, transfer['access_until'],
                                       expected_version=expected_version, current_version=metadata['asset_version'])
            if key is not None:
                op = operation_key(key)
                digest = fingerprint(dict(asset_id=str(asset['id']), asset_version=expected_version))
                if repo.receipt('portableIssueTicket', op, digest) is None:
                    repo.remember('portableIssueTicket', op, digest, 'media_asset', asset['id'], {}, 200)
            count = repo.execute('SELECT count(*) FROM media_tickets WHERE owner_id=:owner AND expires_at>clock_timestamp()').scalar_one()
            if count >= self.media.policy.max_pending * 4:
                raise AuthRejected('RATE_LIMITED', 429)
            token = secrets.token_urlsafe(32)
            repo.execute('''INSERT INTO media_tickets(id,owner_id,asset_id,expires_at,session_id,
                security_epoch,asset_revision,purpose,token_hash)
                VALUES(:id,:owner,:asset,:deadline,:session,:epoch,:revision,'download',:hash)''',
                id=uuid4(), asset=asset['id'], deadline=deadline, session=principal.session_id,
                epoch=principal.security_epoch, revision=asset['revision'], hash=hashlib.sha256(token.encode()).hexdigest())
            return dict(**metadata, ticket=token, expires_at=deadline)

    def _download(self, ctx, asset_id, ticket, range_header, *, read):
        if not isinstance(ticket, str) or len(ticket) != 43:
            raise AuthRejected('FORBIDDEN', 403)
        with self._transaction(ctx) as (life, repo, principal):
            asset, pinned, _ = self._export_asset(life, repo, identifier(asset_id))
            allowed = repo.execute('''SELECT EXISTS(SELECT FROM media_tickets WHERE owner_id=:owner AND asset_id=:id
                AND session_id=:session AND token_hash=:hash AND security_epoch=:epoch AND asset_revision=:revision
                AND purpose='download' AND expires_at>clock_timestamp())''',
                id=asset['id'], session=principal.session_id, epoch=principal.security_epoch,
                revision=asset['revision'], hash=hashlib.sha256(ticket.encode()).hexdigest()).scalar_one()
            if not allowed:
                raise AuthRejected('FORBIDDEN', 403)
            try:
                first, last, partial = byte_range(range_header, pinned.byte_length)
            except ValueError:
                raise MediaRangeRejected(pinned.byte_length) from None
            if not partial or last - first + 1 > PORTABLE_V21.part_bytes:
                raise MediaRangeRejected(pinned.byte_length)
            if not read:
                return dict(first=first, last=last, partial=True, length=pinned.byte_length, mime=asset['mime_type'])
            try:
                return self.media.store.read(pinned, first, last)
            except ObjectUnavailable:
                raise AuthRejected('STORAGE_UNAVAILABLE', 503) from None

    def download(self, ctx, asset_id, ticket, range_header=None):
        return self._download(ctx, asset_id, ticket, range_header, read=False)

    def read_range(self, ctx, asset_id, ticket, range_header):
        return self._download(ctx, asset_id, ticket, range_header, read=True)

    def read_block(self, ctx, asset_id, ticket, first, last):
        return self.read_range(ctx, asset_id, ticket, f'bytes={first}-{last}')

    def maintain(self):
        before = self.deletion.queue.portable_maintenance()
        self.deletion.expire_due()
        return before
