"""Owner-scoped manifests and minimized receipts inside the existing UoW."""
from uuid import uuid4

from sqlalchemy import text

from ..domain.auth import AuthRejected
from ..domain.data import Resource
from ..domain.media import media_state


class PgMediaRepository:
    def __init__(self, tx, owner_id):
        self.tx, self.connection, self.owner_id = tx, tx.connection, owner_id

    def execute(self, sql, **parameters):
        return self.connection.execute(text(sql), dict(owner=self.owner_id, **parameters))

    def now(self):
        return self.execute('SELECT clock_timestamp()').scalar_one()

    def ancestors(self, asset):
        if asset['class_session_id']:
            # Reuse the catalog's ancestor-first lock order and tombstone checks.
            self.tx.catalog._load(Resource.CLASS_SESSION, asset['class_session_id'], share=True)

    def _require_portable_access(self, asset):
        if asset['kind'] != 'import_package' or not self.execute(
                "SELECT to_regclass('public.portable_transfer_media') IS NOT NULL").scalar_one():
            return
        closed = self.execute('''SELECT EXISTS(SELECT FROM portable_transfer_media p JOIN portable_transfers t
            ON (p.owner_id,p.transfer_id)=(t.owner_id,t.id) WHERE p.owner_id=:owner AND p.asset_id=:id
                AND (t.state='closed' OR t.access_until<=clock_timestamp()))''', id=asset['id']).scalar_one()
        if closed:
            raise AuthRejected('GONE', 410)

    def asset(self, asset_id, *, available=False, lock=True):
        row = self.execute('SELECT * FROM media_assets WHERE owner_id=:owner AND id=:id', id=asset_id).mappings().one_or_none()
        if row is None:
            raise AuthRejected('NOT_FOUND', 404)
        self.ancestors(row)
        self._require_portable_access(row)
        if lock:
            row = self.execute('SELECT * FROM media_assets WHERE owner_id=:owner AND id=:id FOR UPDATE', id=asset_id).mappings().one()
            self._require_portable_access(row)
        row = dict(row, byte_length=row['size_bytes'], state=media_state(row['status'],row['access_revoked_at']))
        if (row['deleted_at'] or row['access_revoked_at'] or row['state'] in ('pending_delete','delete_failed','deleted')
                or (row['expires_at'] and row['expires_at'] <= self.now())
                or (row['hard_expire_at'] and row['hard_expire_at'] <= self.now())):
            raise AuthRejected('GONE', 410)
        if available and row['state'] != 'available':
            raise AuthRejected('NOT_FOUND', 404)
        return row

    def upload(self, upload_id, *, lock=True):
        upload = self.execute('SELECT * FROM media_uploads WHERE owner_id=:owner AND id=:id', id=upload_id).mappings().one_or_none()
        if upload is None:
            raise AuthRejected('NOT_FOUND', 404)
        asset = self.asset(upload['asset_id'], lock=lock)
        if lock:
            upload = self.execute('SELECT * FROM media_uploads WHERE owner_id=:owner AND id=:id FOR UPDATE', id=upload_id).mappings().one()
        if upload['state'] == 'rejected' or upload['expires_at'] <= self.now():
            raise AuthRejected('GONE', 410)
        return upload, asset

    def receipt(self, operation, key, fingerprint):
        return self.tx.receipts.lock_and_get(operation, key, fingerprint)

    def remember(self, operation, key, fingerprint, resource_type, resource_id, body, status):
        import json
        self.execute('''INSERT INTO operation_receipts
            (id,owner_id,operation,op_id,request_hash,resource_type,resource_id,response_status,response_body)
            VALUES(:id,:owner,:operation,:key,:hash,:type,:resource,:status,CAST(:body AS jsonb))''',
            id=uuid4(), operation=operation, key=key, hash=fingerprint, type=resource_type,
            resource=resource_id, status=status, body=json.dumps(body))

    def create(self, body, key, fingerprint, policy, *, max_import_assets=None, replay_lock=True):
        # Serialize capacity checks for concurrent uploads from the same owner.
        self.execute("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:owner AS text)||'media',0))")
        receipt = self.receipt('createMediaUpload', key, fingerprint)
        if receipt:
            return self.upload(receipt['resource_id'], lock=replay_lock)
        if max_import_assets is not None:
            reserved = self.execute("""SELECT count(*) FROM media_assets
                WHERE owner_id=:owner AND kind='import_package' AND status<>'deleted'""").scalar_one()
            if reserved >= max_import_assets:
                raise AuthRejected('CAPACITY_EXCEEDED', 413)
        pending = self.execute("SELECT count(*) FROM media_uploads WHERE owner_id=:owner AND state IN ('staging','uploaded') AND expires_at>clock_timestamp()").scalar_one()
        if pending >= policy.max_pending:
            raise AuthRejected('RATE_LIMITED', 429)
        asset_id, upload_id = uuid4(), uuid4()
        resource = dict(class_session_id=body['class_session_id'])
        self.ancestors(resource)
        object_key = f'media/{self.owner_id.hex}/{upload_id.hex}/{body["sha256"]}'
        expires = self.now() + policy.upload_lifetime
        self.execute('''INSERT INTO media_assets(id,owner_id,class_session_id,kind,sha256,size_bytes,mime_type,object_key,expires_at)
            VALUES(:id,:owner,:classroom,:kind,:sha,:length,:mime,:object_key,:expires)''',
            id=asset_id, classroom=body['class_session_id'], kind=body['purpose'], sha=body['sha256'],
            length=body['byte_length'], mime=body['mime_type'], object_key=object_key,
            expires=expires if max_import_assets is not None else None)
        self.execute('''INSERT INTO media_uploads(id,owner_id,asset_id,expires_at)
            VALUES(:id,:owner,:asset,:expires)''', id=upload_id, asset=asset_id, expires=expires)
        self.remember('createMediaUpload', key, fingerprint, 'media_upload', upload_id, {'upload_id': str(upload_id)}, 201)
        return self.upload(upload_id)

    def attach(self, upload, asset, obj):
        self.execute('''UPDATE media_assets SET object_etag=:etag,object_version=:version,
            first_cloud_received_at=coalesce(first_cloud_received_at,:received),
            status=CASE WHEN status='staging' THEN 'verified' ELSE status END
            WHERE owner_id=:owner AND id=:id''', id=asset['id'], etag=obj.etag, version=obj.version_id, received=obj.received_at)
        if upload['state'] != 'verified':
            self.execute("UPDATE media_uploads SET state='uploaded' WHERE owner_id=:owner AND id=:id", id=upload['id'])

    def complete(self, upload, asset):
        if asset['state'] != 'available':
            self.execute("UPDATE media_assets SET status='committed',confirmed_at=clock_timestamp(),revision=revision+1,updated_at=clock_timestamp() WHERE owner_id=:owner AND id=:id", id=asset['id'])
        self.execute("UPDATE media_uploads SET state='verified' WHERE owner_id=:owner AND id=:id", id=upload['id'])
        return self.asset(asset['id'], available=True)
