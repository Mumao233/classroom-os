"""Deletion persistence: the owner-scoped repository and two narrow system identities.

PgDeletionRepository runs inside the existing owner unit of work. PgDeletionQueue and
PgRestoreOperator connect as separate restricted logins that can only execute the
fixed functions of migration 0010; neither can read a private table.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json
from uuid import UUID, uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from ..domain.data import Gone, NotFound, Resource
from ..domain.deletion import CATALOG_SCOPES, DeletionIntent, ScopeExpanded, SettleWindowOpen, StaleDeletionFence

_LIVE = "('staging','verified','committed')"
_ROW = '''SELECT d.id,d.created_at,d.scope_type,d.scope_id,d.state,d.access_revoked_at,d.completed_at,d.last_error_code,
    coalesce(x.revision,1) AS revision,coalesce(x.updated_at,d.created_at) AS updated_at,x.ledger_generation,x.intent_id
  FROM deletion_requests d LEFT JOIN deletion_executions x ON x.owner_id=d.owner_id AND x.request_id=d.id
  WHERE d.owner_id=:owner AND d.id=:id'''


def _payload(intent: DeletionIntent, generation, settle_seconds):
    record = intent.record()
    del record['members'], record['courses']  # ledger evidence; apply() checks them against the rows
    return json.dumps(dict(record, generation=generation, settle_seconds=settle_seconds))


class PgDeletionRepository:
    def __init__(self, tx, owner_id):
        self.tx, self.connection, self.owner_id = tx, tx.connection, owner_id

    def execute(self, sql, **parameters):
        return self.connection.execute(text(sql), dict(owner=self.owner_id, **parameters))

    def request(self, request_id):
        return self.execute(_ROW, id=request_id).mappings().one_or_none()

    def lock(self, intent: DeletionIntent):
        """Serialize every application of one intent for the rest of the transaction."""
        self.execute('SELECT pg_advisory_xact_lock(hashtextextended(CAST(:name AS text),0))', name=f'deletion:{intent.intent_id}')

    def lock_root(self, scope_type, scope_id):
        """Freeze a cascade's membership for the rest of the transaction.

        Creating a child under a course or semester, or moving one into or out of it,
        takes FOR SHARE on that row; FOR UPDATE here makes all of them wait. The parent
        is locked first, in the catalog's ancestor-before-target order. A root this
        database does not hold has no children that could change.
        """
        if scope_type == 'course':
            parent = self.execute('SELECT semester_id FROM courses WHERE owner_id=:owner AND id=:scope',
                                  scope=scope_id).scalar_one_or_none()
            if parent is not None:
                self.execute('SELECT 1 FROM semesters WHERE owner_id=:owner AND id=:parent FOR SHARE', parent=parent)
            self.execute('SELECT 1 FROM courses WHERE owner_id=:owner AND id=:scope FOR UPDATE', scope=scope_id)
        elif scope_type == 'semester':
            self.execute('SELECT 1 FROM semesters WHERE owner_id=:owner AND id=:scope FOR UPDATE', scope=scope_id)

    def descendants(self, scope_type, scope_id):
        """(courses, classrooms) a cascade covers. Works on a tombstoned root, whose children cannot move."""
        if scope_type == 'course':
            courses, where = (), 's.course_id=:scope'
        elif scope_type == 'semester':
            courses = tuple(self.execute('SELECT c.id FROM courses c WHERE c.owner_id=:owner AND c.semester_id=:scope ORDER BY c.id',
                                         scope=scope_id).scalars())
            where = 's.course_id IN (SELECT c.id FROM courses c WHERE c.owner_id=:owner AND c.semester_id=:scope)'
        else:
            return (), ()
        return courses, tuple(self.execute(
            f'SELECT s.id FROM class_sessions s WHERE s.owner_id=:owner AND {where} ORDER BY s.id', scope=scope_id).scalars())

    def impact(self, scope_type, scope_id, *, lock=False):
        """Current version and extent of a deletion; raises NotFound or Gone like any read.

        With ``lock`` the target, its descendants and their media stay locked until the
        transaction ends, so the confirmed version and extent are what gets revoked:
        an edit, a move, a new classroom or a new upload has to wait.
        """
        if scope_type == 'semester':
            resource = Resource.SEMESTER
            sessions = '''s.course_id IN (SELECT c.id FROM courses c WHERE c.owner_id=:owner AND c.semester_id=:scope)'''
        elif scope_type == 'course':
            resource = Resource.COURSE
            sessions = 's.course_id=:scope'
        elif scope_type in ('class_session', 'audio'):
            resource = Resource.CLASS_SESSION
            sessions = 's.id=:scope'
        else:
            raise ValueError('scope has no preview')
        target = self.tx.catalog._load(resource, scope_id, write=lock)
        if lock:
            # Ancestors before descendants, each level in identifier order.
            if scope_type == 'semester':
                self.execute('SELECT 1 FROM courses c WHERE c.owner_id=:owner AND c.semester_id=:scope ORDER BY c.id FOR UPDATE',
                             scope=scope_id)
            self.execute(f'SELECT 1 FROM class_sessions s WHERE s.owner_id=:owner AND {sessions} ORDER BY s.id FOR UPDATE',
                         scope=scope_id)
            self.execute(f'''SELECT 1 FROM media_assets a WHERE a.owner_id=:owner AND a.class_session_id IN
                (SELECT s.id FROM class_sessions s WHERE s.owner_id=:owner AND {sessions}) ORDER BY a.id FOR UPDATE''',
                scope=scope_id)
        count = 0 if scope_type == 'audio' else self.execute(
            f'SELECT count(*) FROM class_sessions s WHERE s.owner_id=:owner AND s.deleted_at IS NULL AND {sessions}',
            scope=scope_id).scalar_one()
        audio = "AND a.kind='audio_chunk'" if scope_type == 'audio' else ''
        size = self.execute(f'''SELECT coalesce(sum(a.size_bytes),0) FROM media_assets a
            WHERE a.owner_id=:owner AND a.status IN {_LIVE} {audio} AND a.class_session_id IN
              (SELECT s.id FROM class_sessions s WHERE s.owner_id=:owner AND {sessions})''', scope=scope_id).scalar_one()
        return dict(revision=target.revision, class_session_count=count, media_bytes=int(size))

    def apply(self, intent: DeletionIntent, generation, settle_seconds):
        """Idempotently revoke access for one intent; all effects commit together.

        ``generation`` is the independent ledger position, or None when the ledger could
        not accept the request: access is then revoked locally and nothing is physically
        processed until a later call supplies the position.
        """
        if intent.owner_id != self.owner_id:
            raise ValueError('deletion intent belongs to another owner')
        # Concurrent applications of one intent wait here and then observe the first.
        self.lock(intent)
        account = intent.scope_type == 'account'
        fresh = self.request(intent.request_id) is None
        if fresh and intent.scope_type in ('course', 'semester'):
            # The accepted deletion covered exactly the recorded descendants. A copy that
            # holds more under this root (restored from before one of them moved away)
            # must not have them deleted on the strength of this intent. The root stays
            # locked until commit, so what is checked here is what gets revoked below.
            self.lock_root(intent.scope_type, intent.scope_id)
            courses, rooms = self.descendants(intent.scope_type, intent.scope_id)
            if set(courses) - set(intent.courses) or set(rooms) - set(intent.members):
                raise ScopeExpanded()
        if fresh and (account or intent.scope_type in CATALOG_SCOPES):
            sequence = self.execute('SELECT coalesce(max(ledger_sequence),0)+1 FROM deletion_tombstones WHERE owner_id=:owner').scalar_one()
            if account:
                self.execute('''INSERT INTO deletion_requests(id,owner_id,scope_type,scope_id,state,access_revoked_at)
                    VALUES(:id,:owner,'account',:owner,'access_revoked',clock_timestamp())''', id=intent.request_id)
                self.execute('''INSERT INTO deletion_tombstones(id,owner_id,request_id,resource_type,resource_id,erasure_epoch,ledger_sequence)
                    VALUES(:id,:owner,:request,'account',:owner,1,:sequence)''', id=uuid4(), request=intent.request_id, sequence=sequence)
            else:
                resource = Resource(intent.scope_type)
                try:
                    current = self.tx.catalog._load(resource, intent.scope_id, write=True)
                except (NotFound, Gone):
                    # Replayed intent whose target is already unreachable or was never
                    # restored: there is nothing to tombstone, manifests are still marked.
                    current = None
                if current is not None:
                    self.tx.tombstones.revoke(resource, intent.scope_id, current.revision, sequence,
                                              request_id=intent.request_id)
        self.execute('SELECT classroom_web_owner_revoke_media(CAST(:intent AS jsonb))',
                     intent=_payload(intent, generation, settle_seconds))
        if fresh:
            # A course or classroom the accepted cascade covered but this copy holds outside
            # the tombstoned root (restored from before a move, or the root is not in the
            # copy at all) is still readable here. Revoke it as its own request; it has no
            # ledger position yet, so nothing opens until reconciliation records one.
            for course in intent.courses:
                try:
                    self.tx.catalog._load(Resource.COURSE, course, write=True)
                except (NotFound, Gone):
                    continue
                self.lock_root('course', course)
                rooms = self.descendants('course', course)[1]
                if set(rooms) - set(intent.members):
                    raise ScopeExpanded()
                self.apply(DeletionIntent.for_scope(self.owner_id, 'course', course, members=rooms), None, settle_seconds)
            for member in intent.members:
                try:
                    self.tx.catalog._load(Resource.CLASS_SESSION, member, write=True)
                except (NotFound, Gone):
                    continue
                self.apply(DeletionIntent.for_scope(self.owner_id, 'class_session', member), None, settle_seconds)
        row = self.request(intent.request_id)
        if account and fresh:
            # Last statement: the owner context loses all access from here on.
            self.execute('SELECT classroom_web_seal_account(:request)', request=intent.request_id)
        return row

    def remember(self, operation, key, fingerprint, request_id, status=202):
        """Bind the idempotency key to this request. 503 marks a binding made while the
        ledger had not accepted the request; the same key may still complete it."""
        self.execute('''INSERT INTO operation_receipts
            (id,owner_id,operation,op_id,request_hash,resource_type,resource_id,response_status,response_body)
            VALUES(:id,:owner,:operation,:key,:hash,'deletion_request',:resource,:status,CAST(:body AS jsonb))''',
            id=uuid4(), operation=operation, key=key, hash=fingerprint, resource=request_id, status=status,
            body=json.dumps({'deletion_id': str(request_id)}))


@dataclass(frozen=True)
class DeletionClaim:
    request_id: UUID
    owner_id: UUID
    scope_type: str
    lease_token: int
    attempt: int


class _SystemIdentity:
    GROUP = ''

    def __init__(self, url):
        if make_url(url).drivername != 'postgresql+psycopg':
            raise ValueError('explicit PostgreSQL URL required')
        self.engine = create_engine(url, pool_size=2, max_overflow=0, pool_pre_ping=True, hide_parameters=True)

    def _check(self, connection):
        safe = connection.scalar(text("""
            SELECT NOT r.rolsuper AND NOT r.rolbypassrls
              AND pg_has_role(current_user, :role, 'USAGE')
              AND NOT pg_has_role(current_user,'classroom_web_runtime','USAGE')
              AND NOT pg_has_role(current_user,'classroom_web_eraser','USAGE')
              AND NOT has_table_privilege(current_user,'public.media_assets','SELECT')
              AND NOT has_table_privilege(current_user,'public.deletion_requests','SELECT')
            FROM pg_roles r WHERE r.rolname=current_user"""), {'role': self.GROUP})
        if safe is not True:
            raise PermissionError('a separate restricted deletion identity is required')

    @contextmanager
    def _session(self):
        with self.engine.begin() as connection:
            self._check(connection)
            connection.execute(text("SET LOCAL search_path = public, pg_temp"))
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SET LOCAL statement_timeout = '30s'"))
            yield connection

    def _call(self, sql, **parameters):
        with self._session() as connection:
            try:
                return connection.execute(text(sql), parameters).mappings().all()
            except DBAPIError as error:
                state = getattr(error.orig, 'sqlstate', None)
                if state == 'CWD01':
                    raise StaleDeletionFence() from None
                if state == 'CWD02':
                    raise SettleWindowOpen() from None
                raise

    def ledger_state(self):
        return self._call('SELECT * FROM classroom_web_deletion_ledger_state()')

    def audit(self):
        return self._call('SELECT classroom_web_deletion_audit() AS audit')[0]['audit']

    def dispose(self):
        self.engine.dispose()


class PgDeletionQueue(_SystemIdentity):
    """Lease-fenced physical deletion queue; sees identifiers and object keys only."""
    GROUP = 'classroom_web_deletion_worker'

    def portable_maintenance(self):
        return dict(self._call('SELECT * FROM classroom_web_portable_gate()')[0])

    def claim(self, worker, lease_seconds):
        rows = self._call('SELECT * FROM classroom_web_claim_deletion(:worker,:seconds)', worker=worker, seconds=lease_seconds)
        return DeletionClaim(**rows[0]) if rows else None

    def inventory(self, claim):
        return [(row['asset_row'], row['object_key']) for row in self._call(
            'SELECT * FROM classroom_web_deletion_inventory(:request,:token)', request=claim.request_id, token=claim.lease_token)]

    def record(self, claim, asset_row, outcome, code=None):
        self._call('SELECT classroom_web_record_deletion(:request,:token,:asset,:outcome,:code)',
                   request=claim.request_id, token=claim.lease_token, asset=asset_row, outcome=outcome, code=code)

    def finish(self, claim, backoff_seconds):
        return self._call('SELECT classroom_web_finish_deletion(:request,:token,:backoff) AS state',
                          request=claim.request_id, token=claim.lease_token, backoff=backoff_seconds)[0]['state']

    def expired(self, batch=50):
        return [(row['owner_id'], row['asset_id']) for row in self._call(
            'SELECT * FROM classroom_web_expired_media(:batch)', batch=batch)]

    def expire(self, intent, generation, settle_seconds):
        self._call('SELECT classroom_web_expire_media(CAST(:intent AS jsonb))',
                   intent=_payload(intent, generation, settle_seconds))

    @contextmanager
    def expiring(self, owner_id, asset_id):
        """Lock one asset and recheck its deadline; yields (eligible, expire).

        Lock, the caller's ledger append and ``expire`` share one transaction, so the
        asset cannot leave its expired state between the check and the revocation.
        """
        with self._session() as connection:
            eligible = connection.execute(text('SELECT classroom_web_lock_expiry(:owner,:asset)'),
                                          dict(owner=owner_id, asset=asset_id)).scalar_one()

            def expire(intent, generation, settle_seconds):
                if intent.owner_id != owner_id or intent.scope_id != asset_id:
                    raise ValueError('expiry intent does not match the locked asset')
                connection.execute(text('SELECT classroom_web_expire_media(CAST(:intent AS jsonb))'),
                                   dict(intent=_payload(intent, generation, settle_seconds)))
            yield eligible, expire

    def accept_covered(self, intent, generation, settle_seconds):
        self._call('SELECT classroom_web_accept_covered(CAST(:intent AS jsonb))',
                   intent=_payload(intent, generation, settle_seconds))

    def owner_state(self, owner_id):
        return self._call('SELECT classroom_web_owner_state(:owner) AS state', owner=owner_id)[0]['state']

    def key_state(self, key):
        return self._call('SELECT classroom_web_object_key_state(:key) AS state', key=key)[0]['state']


class PgRestoreOperator(_SystemIdentity):
    """Used only by the restore procedure, on a database that is not serving requests."""
    GROUP = 'classroom_web_restore_operator'

    def seal(self):
        return self._call('SELECT classroom_web_restore_seal() AS sealed')[0]['sealed']

    def block(self, owners):
        return self._call('SELECT classroom_web_restore_block(CAST(:owners AS uuid[])) AS blocked',
                          owners=[UUID(str(owner)) for owner in owners])[0]['blocked']
