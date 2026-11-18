"""Private media upload, recovery and per-request download authorization."""
from contextlib import contextmanager
import hashlib
import json
import secrets
from uuid import UUID, uuid4

from ..domain.auth import AuthRejected
from ..domain.data import Gone, NotFound, RevisionConflict
from ..domain.identity import AuthorizationDenied
from ..domain.media import MediaPolicy, ObjectUnavailable, StoredObject, byte_range
from ..domain.tasks import IdempotencyMismatch
from ..infrastructure.media_repository import PgMediaRepository


class MediaRangeRejected(AuthRejected):
    def __init__(self, length):
        super().__init__('VALIDATION_FAILED', 416)
        self.length = length


def identifier(value):
    try:
        result = UUID(str(value))
        if str(result) != str(value):
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise AuthRejected('VALIDATION_FAILED', 422) from None


def fingerprint(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def upload_dto(upload, asset):
    return dict(id=str(upload['id']), asset_id=str(asset['id']),
        upload_path=f'/api/web/v1/media/uploads/{upload["id"]}/content',
        state=upload['state'], expires_at=upload['expires_at'].isoformat())


def asset_dto(asset):
    result = {key: asset[key] for key in ('id','revision','created_at','updated_at',
        'class_session_id','kind','sha256','byte_length','mime_type','state','expires_at')}
    result['id'] = str(asset['id'])
    result['class_session_id'] = str(asset['class_session_id']) if asset['class_session_id'] else None
    for key in ('created_at','updated_at','expires_at'):
        result[key] = asset[key].isoformat() if asset[key] else None
    return result


def stored(asset):
    return StoredObject(asset['object_key'], asset['object_etag'], asset['object_version'],
                        asset['byte_length'], asset['first_cloud_received_at'])


class MediaService:
    # Experimental staging admission. Production media options stay independent.
    IMPORT_PACKAGE_MAX_BYTES = 8_000_000
    IMPORT_PACKAGE_MAX_ASSETS = 4

    def __init__(self, *, auth, database, store, policy=None, expire_import_packages=False):
        store.validate_private()
        if expire_import_packages and not getattr(store, 'synthetic_loopback', False):
            raise ValueError('experimental package expiry requires synthetic loopback storage')
        self.auth, self.database, self.store = auth, database, store
        self.policy = policy or MediaPolicy()
        self.expire_import_packages = expire_import_packages

    @contextmanager
    def transaction(self, ctx, *, write=False):
        try:
            if write:
                self.auth.require_csrf(ctx, 'session')
            principal = self.auth.principal(ctx)
            with self.database.authorized_transaction(principal, activity=True) as tx:
                yield PgMediaRepository(tx, principal.user_id), principal
        except (NotFound,):
            raise AuthRejected('NOT_FOUND', 404) from None
        except Gone:
            raise AuthRejected('GONE', 410) from None
        except (RevisionConflict, IdempotencyMismatch):
            raise AuthRejected('IDEMPOTENCY_MISMATCH', 409) from None
        except AuthorizationDenied:
            raise AuthRejected() from None
        except ObjectUnavailable:
            raise AuthRejected('STORAGE_UNAVAILABLE', 503) from None

    def create(self, ctx, key, body):
        import_package = self.expire_import_packages and body['purpose'] == 'import_package'
        if import_package and body['byte_length'] > self.IMPORT_PACKAGE_MAX_BYTES:
            raise AuthRejected('CAPACITY_EXCEEDED', 413)
        if body['byte_length'] > self.policy.max_bytes:
            raise AuthRejected('VALIDATION_FAILED', 422)
        if ((body['purpose'] == 'slide') != body['mime_type'].startswith('image/')
                or (body['purpose'] == 'slide' and body['class_session_id'] is None)):
            raise AuthRejected('VALIDATION_FAILED', 422)
        value = dict(body)
        value['class_session_id'] = identifier(body['class_session_id']) if body['class_session_id'] else None
        with self.transaction(ctx, write=True) as (repo, _):
            upload, asset = repo.create(value, identifier(key), fingerprint(body), self.policy,
                                        max_import_assets=self.IMPORT_PACKAGE_MAX_ASSETS if import_package else None)
            return upload_dto(upload, asset)

    def descriptor(self, ctx, upload_id):
        with self.transaction(ctx, write=True) as (repo, _):
            _, asset = repo.upload(identifier(upload_id))
            return dict(length=asset['byte_length'], sha256=asset['sha256'], mime=asset['mime_type'])

    def verify_content(self, file, descriptor):
        digest = hashlib.sha256()
        length = 0
        file.seek(0)
        head = file.read(16)
        file.seek(0)
        for block in iter(lambda: file.read(self.policy.block_bytes), b''):
            length += len(block)
            digest.update(block)
        if length != descriptor['length'] or digest.hexdigest() != descriptor['sha256']:
            raise AuthRejected('VALIDATION_FAILED', 422)
        mime = descriptor['mime']
        signatures = {'image/png': head.startswith(b'\x89PNG\r\n\x1a\n'),
            'image/jpeg': head.startswith(b'\xff\xd8\xff'),
            'image/webp': head[:4] == b'RIFF' and head[8:12] == b'WEBP',
            'application/zip': head[:4] in (b'PK\x03\x04', b'PK\x05\x06')}
        if mime == 'application/json':
            file.seek(0)
            def reject_constant(_):
                raise ValueError('nonfinite JSON')
            try:
                json.loads(file.read().decode('utf-8-sig'), parse_constant=reject_constant)
            except (ValueError, UnicodeError, RecursionError):
                raise AuthRejected('VALIDATION_FAILED', 422) from None
        elif not signatures.get(mime, False):
            raise AuthRejected('VALIDATION_FAILED', 422)
        file.seek(0)

    def put(self, ctx, key, upload_id, file):
        with self.transaction(ctx, write=True) as (repo, _):
            upload, asset = repo.upload(identifier(upload_id))
            descriptor = dict(length=asset['byte_length'], sha256=asset['sha256'], mime=asset['mime_type'])
            self.verify_content(file, descriptor)
            digest = fingerprint(dict(upload_id=str(upload['id']), sha256=asset['sha256'], byte_length=asset['byte_length']))
            receipt = repo.receipt('putMediaUpload', identifier(key), digest)
            if receipt is None:
                obj = self.store.put(asset['object_key'], file, asset['byte_length'], asset['sha256'])
                repo.attach(upload, asset, obj)
                repo.remember('putMediaUpload', identifier(key), digest, 'media_upload', upload['id'], {'upload_id': str(upload['id'])}, 200)
            return upload_dto(*repo.upload(upload['id']))

    def complete(self, ctx, key, upload_id, body):
        with self.transaction(ctx, write=True) as (repo, _):
            upload, asset = repo.upload(identifier(upload_id))
            digest = fingerprint(dict(upload_id=str(upload['id']), **body))
            receipt = repo.receipt('completeMediaUpload', identifier(key), digest)
            if body != dict(sha256=asset['sha256'], byte_length=asset['byte_length']):
                raise AuthRejected('VALIDATION_FAILED', 422)
            if receipt is None:
                obj = self.store.inspect(asset['object_key'])
                if obj is None or obj.byte_length != asset['byte_length']:
                    raise AuthRejected('VALIDATION_FAILED', 422)
                # Also recovers a successful object write whose database transaction failed.
                h = hashlib.sha256()
                for first in range(0, obj.byte_length, self.policy.block_bytes):
                    h.update(self.store.read(obj, first, min(obj.byte_length-1, first+self.policy.block_bytes-1)))
                if h.hexdigest() != asset['sha256']:
                    raise AuthRejected('VALIDATION_FAILED', 422)
                repo.attach(upload, asset, obj)
                repo.complete(upload, asset)
                repo.remember('completeMediaUpload', identifier(key), digest, 'media_asset', asset['id'], {'asset_id': str(asset['id'])}, 200)
            return asset_dto(repo.asset(asset['id'], available=True))

    def access(self, ctx, key, asset_id):
        with self.transaction(ctx, write=True) as (repo, principal):
            repo.execute("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:owner AS text)||'media_ticket',0))")
            asset = repo.asset(identifier(asset_id), available=True)
            digest = fingerprint(dict(asset_id=str(asset['id'])))
            if repo.receipt('issueMediaAccess', identifier(key), digest) is None:
                repo.remember('issueMediaAccess', identifier(key), digest, 'media_asset', asset['id'], {'asset_id': str(asset['id'])}, 200)
            live = repo.execute('SELECT count(*) FROM media_tickets WHERE owner_id=:owner AND expires_at>clock_timestamp()').scalar_one()
            if live >= self.policy.max_pending * 4:
                raise AuthRejected('RATE_LIMITED', 429)
            token = secrets.token_urlsafe(32)
            expires = min(repo.now()+self.policy.ticket_lifetime, principal.expires_at)
            for field in ('expires_at','hard_expire_at'):
                if asset[field]:
                    expires = min(expires, asset[field])
            repo.execute('''INSERT INTO media_tickets(id,owner_id,asset_id,expires_at,session_id,security_epoch,asset_revision,purpose,token_hash)
                VALUES(:id,:owner,:asset,:expires,:session,:epoch,:revision,'download',:hash)''',
                id=uuid4(), asset=asset['id'], expires=expires, session=principal.session_id,
                epoch=principal.security_epoch, revision=asset['revision'], hash=hashlib.sha256(token.encode()).hexdigest())
            return dict(download_path=f'/api/web/v1/media/{asset["id"]}/content?ticket={token}', expires_at=expires.isoformat(), requires_session=True)

    def download(self, ctx, asset_id, token, range_header=None):
        with self.transaction(ctx) as (repo, principal):
            asset = repo.asset(identifier(asset_id), available=True)
            if not isinstance(token, str) or len(token) != 43:
                raise AuthRejected('FORBIDDEN', 403)
            ticket = repo.execute('''SELECT * FROM media_tickets WHERE owner_id=:owner AND token_hash=:hash
                AND asset_id=:asset AND session_id=:session AND security_epoch=:epoch
                AND asset_revision=:revision AND purpose='download' AND expires_at>clock_timestamp()''',
                hash=hashlib.sha256(token.encode()).hexdigest(), asset=asset['id'], session=principal.session_id,
                epoch=principal.security_epoch, revision=asset['revision']).mappings().one_or_none()
            if ticket is None:
                raise AuthRejected('FORBIDDEN', 403)
            try:
                first, last, partial = byte_range(range_header, asset['byte_length'])
            except ValueError:
                raise MediaRangeRejected(asset['byte_length']) from None
            return dict(obj=stored(asset), first=first, last=last, partial=partial, length=asset['byte_length'], mime=asset['mime_type'])

    def read_block(self, ctx, asset_id, token, first, last):
        # Fresh authorization for each bounded block also closes an established stream on revocation.
        descriptor = self.download(ctx, asset_id, token, f'bytes={first}-{last}')
        try:
            return self.store.read(descriptor['obj'], first, last)
        except ObjectUnavailable:
            raise AuthRejected('STORAGE_UNAVAILABLE', 503) from None

    def recover(self, ctx, *, limit=20):
        """Bounded authenticated staging scan; retain unresolved objects for retry."""
        if not 1 <= limit <= 100:
            raise ValueError('invalid recovery batch size')
        with self.transaction(ctx, write=True) as (repo, _):
            rows = repo.execute('''SELECT u.id,a.sha256,a.size_bytes AS byte_length FROM media_uploads u
                JOIN media_assets a ON (a.owner_id,a.id)=(u.owner_id,u.asset_id)
                WHERE u.owner_id=:owner AND u.state IN ('staging','uploaded')
                AND u.expires_at>clock_timestamp() ORDER BY u.created_at,u.id LIMIT :limit''', limit=limit).mappings().all()
        results = []
        for row in rows:
            try:
                self.complete(ctx, str(uuid4()), str(row['id']), dict(sha256=row['sha256'],byte_length=row['byte_length']))
                results.append(dict(upload_id=str(row['id']), state='verified'))
            except AuthRejected as error:
                results.append(dict(upload_id=str(row['id']), state='pending', code=error.code))
        return results
