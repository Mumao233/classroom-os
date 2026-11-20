"""Owner-scoped candidate transfer records and PostgreSQL worker fencing."""
from uuid import uuid4

from sqlalchemy import text

from ..domain.auth import AuthRejected
from ..domain.portable import PORTABLE_V21

def ticket_deadline(now, session_until, access_until, *, expected_version, current_version):
    if expected_version is not None and expected_version != current_version:
        raise AuthRejected('SOURCE_CHANGED', 409)
    deadline = min(now + PORTABLE_V21.ticket_age, session_until, access_until)
    if deadline <= now:
        raise AuthRejected('GONE', 410)
    return deadline


def admission_limited():
    error = AuthRejected('RATE_LIMITED', 429)
    error.retry_after_seconds = 30
    return error


class PortableLifecycle:
    """Use inside the current owner transaction. Keep object I/O outside the gate."""

    def __init__(self, tx):
        self.conn = tx.connection
        self.owner = tx.catalog.owner_id

    def rows(self, query, **values):
        return self.conn.execute(text(query), {'owner': self.owner, **values}).mappings()

    def now(self):
        return self.conn.scalar(text('SELECT clock_timestamp()'))

    def gate(self):
        return self.rows('SELECT * FROM classroom_web_portable_admit()').one()

    def transfer(self, identity, *, state=None):
        row = self.rows('''SELECT * FROM portable_transfers
                           WHERE owner_id=:owner AND id=:id''', id=identity).one_or_none()
        if row is None:
            raise AuthRejected('NOT_FOUND', 404)
        if row['state'] == 'closed' or (row['access_until'] and row['access_until'] <= self.now()):
            raise AuthRejected('GONE', 410)
        if state is not None and row['state'] != state:
            raise AuthRejected('SOURCE_UNAVAILABLE', 409)
        return row

    @staticmethod
    def replay(row, digest):
        if row['fingerprint'] != digest:
            raise AuthRejected('IDEMPOTENCY_MISMATCH', 409)
        return row

    def reserve(self, kind, requested_bytes, op_id, digest, *, archive_sha256=None, mime=None):
        if kind not in ('import', 'export') or type(requested_bytes) is not int or requested_bytes < 1:
            raise AuthRejected('VALIDATION_FAILED', 422)
        if requested_bytes > PORTABLE_V21.archive_bytes:
            raise AuthRejected('CAPACITY_EXCEEDED', 413)
        pool = self.gate()
        previous = self.rows('''SELECT * FROM portable_transfers
            WHERE owner_id=:owner AND kind=:kind AND op_id=:op''', kind=kind, op=op_id).one_or_none()
        if previous:
            return self.replay(previous, digest)
        occupied = self.rows('''SELECT EXISTS(SELECT FROM portable_transfers
            WHERE owner_id=:owner AND kind=:kind
            AND ((kind='import' AND state='uploading') OR (kind='export' AND state<>'closed'))) AS occupied''', kind=kind).one()['occupied']
        if occupied or pool[kind + '_bytes'] + requested_bytes > PORTABLE_V21.pool_bytes:
            raise admission_limited()
        return self.rows('''INSERT INTO portable_transfers
            (id,owner_id,kind,op_id,fingerprint,reserved_bytes,state,access_until,archive_sha256,mime_type,policy_version)
            VALUES(:id,:owner,:kind,:op,:digest,:n,:state,:until,:sha,:mime,:policy) RETURNING *''',
            id=uuid4(), kind=kind, op=op_id, digest=digest, n=requested_bytes,
            state='uploading' if kind == 'import' else 'building', sha=archive_sha256, mime=mime,
            until=self.now() + PORTABLE_V21.access_age if kind == 'import' else None, policy=PORTABLE_V21.version).one()

    def parts(self, transfer_id):
        return self.rows('''SELECT a.*,a.size_bytes AS byte_length,p.part_number
            FROM portable_transfer_media p JOIN media_assets a ON (a.owner_id,a.id)=(p.owner_id,p.asset_id)
            WHERE p.owner_id=:owner AND p.transfer_id=:id ORDER BY p.part_number''', id=transfer_id).all()

    def stage(self, transfer_id, number, length, digest, mime):
        self.gate()
        transfer = self.transfer(transfer_id)
        parts = self.parts(transfer_id)
        for part in parts:
            if part['part_number'] == number:
                if (part['size_bytes'], part['sha256'], part['mime_type']) != (length, digest, mime):
                    raise AuthRejected('IDEMPOTENCY_MISMATCH', 409)
                return part
        if transfer['state'] not in ('building', 'uploading'):
            raise AuthRejected('SOURCE_UNAVAILABLE', 409)
        if sum(part['size_bytes'] for part in parts) + length > transfer['reserved_bytes']:
            raise AuthRejected('CAPACITY_EXCEEDED', 413)
        asset = uuid4()
        row = self.rows('''INSERT INTO media_assets
            (id,owner_id,kind,sha256,size_bytes,mime_type,object_key,expires_at)
            VALUES(:asset,:owner,:kind,:sha,:size,:mime,:key,:until)
            RETURNING *,size_bytes AS byte_length''',
            asset=asset, kind='import_package' if transfer['kind'] == 'import' else 'export',
            sha=digest, size=length, mime=mime, key=f'media/{self.owner.hex}/{asset.hex}/{digest}',
            until=transfer['access_until']).one()
        self.rows('''INSERT INTO portable_transfer_media(owner_id,transfer_id,asset_id,part_number)
            VALUES(:owner,:transfer,:asset,:number) RETURNING asset_id''',
            transfer=transfer_id, asset=asset, number=number).one()
        return row

    def attach(self, transfer_id, part, obj):
        self.gate()
        self.transfer(transfer_id)
        asset = self.rows('''UPDATE media_assets SET object_etag=:etag, object_version=:version,
                first_cloud_received_at=:received,status='verified'
            WHERE owner_id=:owner AND id=:asset AND status='staging' RETURNING id''',
            asset=part['id'], etag=obj.etag, version=obj.version_id, received=obj.received_at).one_or_none()
        if asset is None:
            existing = next(p for p in self.parts(transfer_id) if p['id'] == part['id'])
            if existing['status'] != 'verified' or (existing['object_etag'], existing['object_version']) != (obj.etag, obj.version_id):
                raise AuthRejected('SOURCE_CHANGED', 409)

    def mark_verified(self, transfer_id):
        self.gate()
        transfer = self.transfer(transfer_id)
        parts = self.parts(transfer_id)
        if (transfer['kind'] != 'import' or transfer['state'] not in ('uploading', 'verified')
                or not parts or any(p['status'] != 'verified' for p in parts)
                or sum(p['size_bytes'] for p in parts) != transfer['reserved_bytes']):
            raise AuthRejected('SOURCE_UNAVAILABLE', 409)
        self.rows("UPDATE portable_transfers SET state='verified' WHERE owner_id=:owner AND id=:id RETURNING id",
                  id=transfer_id).one()

    def claim(self, transfer_id, op_id, digest, worker_id, *, destination=None):
        budget = self.gate()
        previous = self.rows('SELECT * FROM portable_slots WHERE owner_id=:owner AND op_id=:op', op=op_id).one_or_none()
        if previous:
            return self.replay(previous, digest)
        transfer = self.transfer(transfer_id)
        if transfer['state'] not in ('verified', 'building'):
            raise AuthRejected('SOURCE_UNAVAILABLE', 409)
        owner_busy = self.rows('''SELECT EXISTS(SELECT FROM portable_slots WHERE owner_id=:owner
            AND state IN ('running','waiting')) AS busy''').one()['busy']
        if budget['slots'] >= PORTABLE_V21.slots or owner_busy:
            raise admission_limited()
        if destination is not None:
            self.rows('''UPDATE portable_transfers SET destination_course_id=:course
                WHERE owner_id=:owner AND id=:id RETURNING id''', id=transfer_id, course=destination).one()
        return self.rows('''INSERT INTO portable_slots
            (id,owner_id,transfer_id,op_id,fingerprint,state,worker_id,deadline)
            VALUES(:id,:owner,:transfer,:op,:digest,'running',:worker,
                   least(CAST(:until AS timestamptz),CAST(:access AS timestamptz))) RETURNING *''',
            id=uuid4(), transfer=transfer_id, op=op_id, digest=digest, worker=worker_id,
            until=self.now() + PORTABLE_V21.worker_lease, access=transfer['access_until']).one()

    def live_work(self, slot_id, worker_id, fence):
        slot = self.rows('SELECT * FROM portable_slots WHERE owner_id=:owner AND id=:id',
                         id=slot_id).one_or_none()
        if slot is None:
            raise AuthRejected('NOT_FOUND', 404)
        if (slot['state'] != 'running' or slot['worker_id'] != worker_id or slot['fence'] != fence
                or slot['deadline'] <= self.now()):
            raise AuthRejected('LEASE_CONFLICT', 409)
        return self.transfer(slot['transfer_id'])

    def heartbeat(self, slot_id, worker_id, fence):
        self.gate()
        transfer = self.live_work(slot_id, worker_id, fence)
        return self.rows('''UPDATE portable_slots SET deadline=least(CAST(:until AS timestamptz),CAST(:access AS timestamptz))
            WHERE owner_id=:owner AND id=:id RETURNING *''',
            id=slot_id, until=self.now() + PORTABLE_V21.worker_lease, access=transfer['access_until']).one()

    def await_confirmation(self, slot_id, worker_id, fence):
        self.gate()
        transfer = self.live_work(slot_id, worker_id, fence)
        if transfer['state'] != 'verified':
            raise AuthRejected('SOURCE_UNAVAILABLE', 409)
        return self.rows('''UPDATE portable_slots SET state='waiting', worker_id=NULL, fence=fence+1,
            deadline=least(CAST(:until AS timestamptz),CAST(:access AS timestamptz))
            WHERE owner_id=:owner AND id=:id RETURNING *''',
            id=slot_id, until=self.now() + PORTABLE_V21.confirmation_lease, access=transfer['access_until']).one()

    def confirm(self, slot_id, worker_id):
        self.gate()
        now = self.now()
        row = self.rows('''UPDATE portable_slots s SET state='running', worker_id=:worker, fence=s.fence+1,
            deadline=least(CAST(:until AS timestamptz),t.access_until) FROM portable_transfers t
            WHERE s.owner_id=:owner AND s.id=:id AND s.state='waiting' AND s.deadline>:now
              AND t.owner_id=s.owner_id AND t.id=s.transfer_id AND t.state='verified' AND t.access_until>:now
            RETURNING s.*''', id=slot_id, worker=worker_id, now=now, until=now + PORTABLE_V21.worker_lease).one_or_none()
        if row is None:
            raise AuthRejected('GONE', 410)
        return row

    def finish(self, slot_id, worker_id, fence, outcome, *, inspected_sha256=None):
        if outcome not in ('completed', 'failed', 'cancelled'):
            raise ValueError('invalid terminal outcome')
        self.gate()
        transfer = self.live_work(slot_id, worker_id, fence)
        now = self.now()
        publish = outcome == 'completed' and transfer['kind'] == 'export'
        if publish:
            parts = self.parts(transfer['id'])
            sources = self.sources(transfer['id'])
            if (len(parts) != 1 or parts[0]['status'] != 'verified'
                    or parts[0]['sha256'] != inspected_sha256 or not sources):
                raise AuthRejected('SOURCE_UNAVAILABLE', 409)
            self.rows('''UPDATE media_assets SET expires_at=:until,
                          revision=revision+1
                WHERE owner_id=:owner AND id=:asset RETURNING id''',
                asset=parts[0]['id'], until=now + PORTABLE_V21.access_age).one()
        self.rows('''UPDATE portable_transfers SET state=:state,access_until=:until
            WHERE owner_id=:owner AND id=:id RETURNING id''',
            id=transfer['id'], state='published' if publish else 'closed',
            until=now + PORTABLE_V21.access_age if publish else min(now, transfer['access_until'] or now)).one()
        return self.rows('''UPDATE portable_slots SET state=:state,worker_id=NULL,fence=fence+1,
            finished_at=:now WHERE owner_id=:owner AND id=:id RETURNING *''',
            state=outcome, now=now, id=slot_id).one()

    def sources(self, transfer_id):
        return [row['class_session_id'] for row in self.rows('''SELECT class_session_id FROM portable_export_sources
            WHERE owner_id=:owner AND transfer_id=:id ORDER BY class_session_id''', id=transfer_id)]

    def bind_sources(self, transfer_id, sources):
        for source in sources:
            self.rows('''INSERT INTO portable_export_sources(owner_id,transfer_id,class_session_id)
                VALUES(:owner,:id,:source) ON CONFLICT DO NOTHING RETURNING class_session_id''',
                id=transfer_id, source=source).all()

    def close(self, transfer_id):
        self.gate()
        return self.rows('''UPDATE portable_transfers SET state='closed',access_until=least(access_until,:now)
            WHERE owner_id=:owner AND id=:id RETURNING *''',
            id=transfer_id, now=self.now()).one_or_none()
