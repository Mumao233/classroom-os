"""Owner-scoped preview using verified private uploads and minimized receipts."""
from datetime import datetime
import threading

from .media import fingerprint, identifier, stored
from ..domain.auth import AuthRejected, AuthReply
from ..domain.data import Resource
from ..domain.portable import InvalidPackage, PackageLimits, inspect_package
from ..test_support import validate_test_database_target


class PortablePreview:
    OPERATION = 'previewImport'
    MAX_RECEIPTS_PER_UPLOAD = 4

    def __init__(self, media, *, limits=None):
        # PR-08c experiments use only isolated synthetic PostgreSQL and S3.
        validate_test_database_target(media.database.engine.url.render_as_string(hide_password=False))
        if not media.expire_import_packages:
            raise ValueError('portable preview requires expiring synthetic package uploads')
        self.media = media
        self.limits = limits or PackageLimits()
        self.slots = threading.BoundedSemaphore(2)

    def preview(self, ctx, key, body):
        upload_id = identifier(body['upload_id'])
        course_id = identifier(body['destination_course_id'])
        op_id = identifier(key)
        if op_id.version != 4:
            raise AuthRejected('VALIDATION_FAILED', 422)
        if not self.slots.acquire(blocking=False):
            raise AuthRejected('RATE_LIMITED', 429)
        try:
            return self._preview(ctx, op_id, upload_id, course_id, fingerprint(body))
        finally:
            self.slots.release()

    def _preview(self, ctx, op_id, upload_id, course_id, request_hash):
        with self.media.transaction(ctx, write=True) as (repo, _):
            receipt = repo.receipt(self.OPERATION, op_id, request_hash)
            # Use the established ancestor-first locking order. Object access
            # and replay both have to pass current authorization.
            repo.tx.catalog._load(Resource.COURSE, course_id, share=True)
            upload, asset = repo.upload(upload_id)
            if asset['kind'] != 'import_package' or asset['class_session_id'] is not None:
                raise AuthRejected('NOT_FOUND', 404)
            if upload['state'] != 'verified' or asset['state'] != 'available':
                raise AuthRejected('SOURCE_UNAVAILABLE', 409)
            if asset['expires_at'] is None:
                raise AuthRejected('SOURCE_UNAVAILABLE', 409)
            if asset['byte_length'] > self.limits.archive_bytes:
                raise AuthRejected('CAPACITY_EXCEEDED', 413)
            obj = stored(asset)
            if receipt:
                result = receipt['response_body']
                if datetime.fromisoformat(result['expires_at']) <= repo.now():
                    raise AuthRejected('GONE', 410)
                current = self.media.store.inspect(obj.key)
                if current is None or (current.etag, current.version_id, current.byte_length) != (
                        obj.etag, obj.version_id, obj.byte_length):
                    raise AuthRejected('SOURCE_CHANGED', 409)
                return AuthReply(result)
            count = repo.execute("""SELECT count(*) FROM operation_receipts
                WHERE owner_id=:owner AND operation=:operation AND resource_id=:id""",
                                 operation=self.OPERATION, id=upload_id).scalar_one()
            if count >= self.MAX_RECEIPTS_PER_UPLOAD:
                raise AuthRejected('CAPACITY_EXCEEDED', 413)

            size = asset['byte_length']
            block = self.media.policy.block_bytes
            content = bytearray()
            for first in range(0, size, block):
                content.extend(self.media.store.read(obj, first, min(size - 1, first + block - 1)))
            if len(content) != size:
                raise AuthRejected('STORAGE_UNAVAILABLE', 503)
            try:
                package = inspect_package(bytes(content), asset['mime_type'], self.limits)
            except InvalidPackage as error:
                code = error.code
                if code == 'PORTABLE_VERSION_UNSUPPORTED':
                    code = 'INCOMPATIBLE_CLIENT'
                raise AuthRejected(code, 413 if code == 'CAPACITY_EXCEEDED' else 422) from None
            if package.archive_sha256 != asset['sha256']:
                raise AuthRejected('SOURCE_CHANGED', 409)
            expires = min(x for x in (upload['expires_at'], asset['expires_at'], asset['hard_expire_at']) if x)
            if expires <= repo.now():
                raise AuthRejected('GONE', 410)
            session = package.session
            review = session.get('review', {})
            review_count = sum(len(review.get(key, [])) for key in ('cards', 'notes', 'marks'))
            impact = (f"Validated {len(session.get('segments', []))} transcript segments and "
                      f"{review_count} review cards, notes and marks.")
            result = dict(id=str(op_id), archive_sha256=package.archive_sha256, class_session_count=1,
                          media_bytes=package.media_bytes, warnings=[impact, *package.warnings],
                          unsupported_fields=list(package.unsupported_fields), expires_at=expires.isoformat())
            repo.remember(self.OPERATION, op_id, request_hash, 'media_upload', upload_id, result, 201)
            return AuthReply(result)
