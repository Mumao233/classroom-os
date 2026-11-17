"""Deletion acceptance, physical erasure, ledger reconciliation and restore.

A user deletion is one database transaction: reserve the idempotency key, lock the
target with its descendants and media, check the confirmed version and extent, record
the intent in the independent ledger, then revoke access and persist the inventory.
``202`` is returned only after both exist. If the ledger cannot accept the intent in
time, access is still revoked locally and the caller receives a retryable ``503``.
The same idempotent application step serves the request path, crash recovery and the
replay that follows a backup restore.
"""
import base64
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import hmac
import json
import time
from uuid import UUID

from sqlalchemy.exc import DBAPIError

from ..domain.auth import AuthRejected, AuthReply
from ..domain.data import Gone, InactiveOwner, NotFound, OwnerContext, RevisionConflict
from ..domain.deletion import (CATALOG_SCOPES, Clock, DeletionIntent, DeletionPolicy, LedgerConflict, LedgerCorrupt,
                               LedgerUnavailable, ScopeExpanded, SettleWindowOpen, StaleDeletionFence, SystemClock, canonical,
                               deletion_dto)
from ..domain.identity import AuthorizationDenied
from ..domain.media import ObjectUnavailable
from ..domain.tasks import IdempotencyMismatch
from ..infrastructure.auth_deadlines import remaining
from ..infrastructure.deletion_repository import PgDeletionRepository

# path -> (scope, {method: operationId}); account deletion is deliberately absent.
ROUTES = {
    '/class-sessions/{class_session_id}/deletion-preview': ('class_session', 'class_session_id'),
    '/class-sessions/{class_session_id}/delete': ('class_session', 'class_session_id'),
    '/class-sessions/{class_session_id}/audio-deletion-preview': ('audio', 'class_session_id'),
    '/class-sessions/{class_session_id}/audio/delete': ('audio', 'class_session_id'),
    '/courses/{course_id}/deletion-preview': ('course', 'course_id'),
    '/courses/{course_id}/delete': ('course', 'course_id'),
    '/semesters/{semester_id}/deletion-preview': ('semester', 'semester_id'),
    '/semesters/{semester_id}/delete': ('semester', 'semester_id'),
    '/deletions/{deletion_id}': (None, 'deletion_id'),
}
OPERATIONS = {'class_session': 'deleteClassSession', 'audio': 'deleteAudio',
              'course': 'deleteCourse', 'semester': 'deleteSemester'}


def identifier(value):
    try:
        result = UUID(str(value))
        if str(result) != str(value):
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise AuthRejected('VALIDATION_FAILED', 422) from None


class DeletionService:
    # Kept back from the request deadline for the revocation that follows the ledger call.
    REVOCATION_RESERVE = 1.0

    def __init__(self, *, auth, database, queue, store, ledger, confirmation_secret: bytes,
                 policy: DeletionPolicy | None = None, clock: Clock | None = None):
        if not isinstance(confirmation_secret, bytes) or len(confirmation_secret) < 32:
            raise ValueError('confirmation secret must contain at least 32 bytes')
        media_endpoint = getattr(getattr(getattr(store, 'client', None), 'meta', None), 'endpoint_url', None)
        if (getattr(ledger, 'bucket', None) is not None and getattr(ledger, 'bucket', None) == getattr(store, 'bucket', None)
                and (media_endpoint or '').rstrip('/') == getattr(ledger, 'endpoint', '')):
            raise ValueError('the deletion ledger must not share the media bucket')
        self.auth, self.database, self.queue, self.store, self.ledger = auth, database, queue, store, ledger
        self.secret, self.policy, self.clock = confirmation_secret, policy or DeletionPolicy(), clock or SystemClock()
        # Closed until reconcile() has verified the ledger and found nothing unapplied.
        self.ready = False

    @property
    def settle_seconds(self):
        return int(self.policy.write_settle.total_seconds())

    # -- request path ------------------------------------------------------------
    @contextmanager
    def transaction(self, ctx, *, write=False):
        try:
            if write:
                self.auth.require_csrf(ctx, 'session')
            principal = self.auth.principal(ctx)
            with self.database.authorized_transaction(principal, activity=True) as tx:
                yield PgDeletionRepository(tx, principal.user_id), principal
        except NotFound:
            raise AuthRejected('NOT_FOUND', 404) from None
        except Gone:
            raise AuthRejected('GONE', 410) from None
        except (RevisionConflict, ScopeExpanded):
            raise AuthRejected('REVISION_CONFLICT', 409) from None
        except IdempotencyMismatch:
            raise AuthRejected('IDEMPOTENCY_MISMATCH', 409) from None
        except AuthorizationDenied:
            raise AuthRejected() from None

    def _signature(self, principal, scope_type, scope_id, impact, expires):
        message = canonical(dict(owner=str(principal.user_id), session=str(principal.session_id), scope_type=scope_type,
                                 scope_id=str(scope_id), revision=impact['revision'], sessions=impact['class_session_count'],
                                 bytes=impact['media_bytes'], expires=expires))
        return base64.urlsafe_b64encode(hmac.digest(self.secret, message, 'sha256')).decode('ascii').rstrip('=')

    def _confirm(self, principal, scope_type, scope_id, impact, token):
        """The token is bound to this session, object version and the extent shown to the user."""
        try:
            expires, signature = token.split('.')
            if not expires.isascii() or not expires.isdecimal() or len(expires) > 12:
                raise ValueError()
            expires = int(expires)
        except (ValueError, AttributeError):
            raise AuthRejected('VALIDATION_FAILED', 422) from None
        expected = self._signature(principal, scope_type, scope_id, impact, expires)
        if expires <= self.clock.now().timestamp() or not hmac.compare_digest(signature, expected):
            raise AuthRejected('VALIDATION_FAILED', 422)

    def preview(self, ctx, scope_type, scope_id):
        scope = identifier(scope_id)
        with self.transaction(ctx) as (repo, principal):
            impact = repo.impact(scope_type, scope)
            expires_at = self.clock.now() + self.policy.confirmation_lifetime
            expires = int(expires_at.timestamp())
            return dict(scope_type=scope_type, scope_id=str(scope), revision=impact['revision'],
                        class_session_count=impact['class_session_count'], media_bytes=impact['media_bytes'],
                        confirmation_token=f'{expires}.{self._signature(principal, scope_type, scope, impact, expires)}',
                        expires_at=expires_at.replace(microsecond=0).isoformat())

    def submit(self, ctx, key, scope_type, scope_id, body):
        if not self.ready:
            raise AuthRejected('SERVICE_DISABLED', 503)
        key, scope, operation = identifier(key), identifier(scope_id), OPERATIONS[scope_type]
        if key.version != 4:
            raise AuthRejected('VALIDATION_FAILED', 422)
        if scope_type in ('course', 'semester'):
            # Moving children to another parent is a product choice this package does
            # not implement; it is refused rather than silently treated as a cascade.
            if body.get('mode') != 'cascade':
                raise AuthRejected('SERVICE_DISABLED', 503)
            if body.get('destination_id') is not None:
                raise AuthRejected('VALIDATION_FAILED', 422)
        digest = hashlib.sha256(canonical(dict(scope_type=scope_type, scope_id=str(scope), body=body))).hexdigest()
        accepted = True
        with self.transaction(ctx, write=True) as (repo, principal):
            # Held to the end of the transaction: a concurrent request with this key
            # waits here and then meets the stored receipt, so it can never reach the
            # ledger with another target.
            receipt = repo.tx.receipts.lock_and_get(operation, key, digest)
            intent = DeletionIntent.for_scope(principal.user_id, scope_type, scope)
            repo.lock(intent)
            existing = repo.request(intent.request_id)
            if existing is not None and existing['ledger_generation'] is not None:
                # A repeated deletion of the same object returns the one existing request.
                if not receipt:
                    repo.remember(operation, key, digest, intent.request_id)
                return deletion_dto(existing)
            # From here the request is new, or its access was revoked while the ledger was
            # away (the key may already be bound to it) and this call completes the acceptance.
            if existing is None:
                # The locks taken here are kept across the ledger call, so what the
                # user confirmed is exactly what is revoked below.
                impact = repo.impact(scope_type, scope, lock=True)
                if impact['revision'] != body['base_revision']:
                    raise AuthRejected('REVISION_CONFLICT', 409)
                self._confirm(principal, scope_type, scope, impact, body['confirmation_token'])
            try:
                courses, rooms = repo.descendants(scope_type, scope)
                intent = DeletionIntent.for_scope(principal.user_id, scope_type, scope, members=rooms, courses=courses)
            except ValueError:
                raise AuthRejected('VALIDATION_FAILED', 422) from None
            left = remaining()
            deadline = None if left is None else (time.monotonic() + left - self.REVOCATION_RESERVE
                                                  - getattr(self.ledger, 'call_seconds', 0))
            try:
                entry = self.ledger.append(intent, deadline=deadline)
            except LedgerUnavailable:
                entry = None
            except (LedgerCorrupt, LedgerConflict):
                self.ready = False
                raise AuthRejected('STORAGE_UNAVAILABLE', 503) from None
            if entry is None:
                # Not durably accepted. Deny access locally now, in this same transaction,
                # and bind the key to this target; the client retries the same key.
                repo.apply(intent, None, self.settle_seconds)
                accepted = False
            else:
                row = repo.apply(entry.intent, entry.generation, self.settle_seconds)
            if not receipt:
                repo.remember(operation, key, digest, intent.request_id, 202 if accepted else 503)
        if not accepted:
            raise AuthRejected('STORAGE_UNAVAILABLE', 503)
        return deletion_dto(row)

    def status(self, ctx, deletion_id):
        with self.transaction(ctx) as (repo, _):
            row = repo.request(identifier(deletion_id))
            if row is None:
                raise AuthRejected('NOT_FOUND', 404)
            return deletion_dto(row)

    def handle(self, path, method, ctx, body, key, params):
        """Entry point for the generic HTTP dispatcher; returns the frozen DTOs."""
        scope_type, parameter = ROUTES[path]
        if scope_type is None:
            return AuthReply(self.status(ctx, params[parameter]))
        if method == 'get':
            return AuthReply(self.preview(ctx, scope_type, params[parameter]))
        return AuthReply(self.submit(ctx, key, scope_type, params[parameter], body))

    # -- internal ports ------------------------------------------------------------
    def _apply(self, intent, generation):
        """Apply an accepted intent without a browser session (worker-style owner context)."""
        if intent.origin == 'expiry':
            if self.queue.owner_state(intent.owner_id) == 'absent':
                # This copy never had the owner (it predates the account): nothing to revoke.
                raise InactiveOwner('owner absent from this database')
            try:
                return self.queue.expire(intent, generation, self.settle_seconds)
            except DBAPIError as error:
                if getattr(error.orig, 'sqlstate', None) != '23514':
                    raise
                # The ledger authorized this expiry but the local row no longer shows the
                # deadline (restored copy): apply it under the owner context instead.
        with self.database.transaction(OwnerContext(intent.owner_id)) as tx:
            PgDeletionRepository(tx, intent.owner_id).apply(intent, generation, self.settle_seconds)

    def erase_account(self, owner: OwnerContext):
        """Internal account erasure port. No HTTP route reaches it in this package:
        deleteAccount re-authentication remains disabled until it is separately approved.

        Unlike other scopes there is no local-only revocation: a sealed account could not
        retry, so an unavailable ledger leaves the account untouched and raises.
        """
        if not isinstance(owner, OwnerContext):
            raise TypeError('an authenticated OwnerContext is required')
        intent = DeletionIntent.for_scope(owner.owner_id, 'account', owner.owner_id)
        generation = self.ledger.append(intent).generation
        try:
            self._apply(intent, generation)
        except InactiveOwner:
            # Already sealed by an earlier call; reconcile() verifies the recorded position.
            if not any(row['intent_id'] == intent.intent_id and row['ledger_generation'] == generation
                       for row in self.queue.ledger_state()):
                raise
        return intent.request_id

    def reconcile(self, *, witness=None, admit=True):
        """Verify the ledger from its first entry and apply every intent the database lacks.

        Covers a crash between ledger append and database commit, requests the ledger
        had not yet accepted, and a database restored from a backup. Any unreadable,
        broken or contradicting state leaves the service closed. ``witness`` is a head
        recorded outside the ledger store; with it a ledger that lost its newest entries
        is refused. ``admit=False`` never opens the service, whatever the outcome.
        """
        self.ready = False
        # Local state first: a deletion accepted between the two reads is then already in
        # the ledger read (and replayed idempotently), never local-only and "ahead".
        local = {row['intent_id']: row for row in self.queue.ledger_state()}
        entries = self.ledger.entries() if witness is None else self.ledger.verify_head(witness)
        recorded = {entry.intent.intent_id: entry for entry in entries}
        for row in local.values():
            entry = recorded.get(row['intent_id'])
            if row['ledger_generation'] is not None and (entry is None or entry.generation != row['ledger_generation']):
                raise LedgerCorrupt('local deletion state is ahead of or contradicts the ledger')
        report = dict(head=len(entries), head_hash=entries[-1].entry_hash if entries else None,
                      applied=[], accepted=[], covered=[], unresolved=[])
        for entry in entries:
            row = local.get(entry.intent.intent_id)
            if row is not None and row['ledger_generation'] == entry.generation:
                continue
            try:
                self._apply(entry.intent, entry.generation)
                report['applied'].append(entry.generation)
            except InactiveOwner:
                # absent: this copy never had the owner, so it holds nothing to revoke.
                # erasing/erased: the account tombstone already revokes every object and
                # its inventory lists all of the owner's media. A disabled owner still
                # holds restorable data, so that case stays unresolved and closed.
                if self.queue.owner_state(entry.intent.owner_id) in ('absent', 'erasing', 'erased'):
                    report['covered'].append(entry.generation)
                else:
                    report['unresolved'].append(dict(generation=entry.generation, reason='InactiveOwner'))
            except (DBAPIError, ScopeExpanded) as error:
                # ScopeExpanded: this copy holds more under a cascade's root than the accepted
                # deletion covered. Nothing was applied; an operator has to resolve it.
                report['unresolved'].append(dict(generation=entry.generation, reason=type(error).__name__))
        # Read again: replay may have revoked classrooms that a cascade covered but this
        # copy held elsewhere; like requests the ledger never accepted, they need a position.
        for row in self.queue.ledger_state():
            if row['ledger_generation'] is None and row['intent_id'] not in recorded:
                # Access was revoked while the ledger was unavailable; complete the acceptance.
                try:
                    if row['origin'] == 'expiry':
                        entry = self.ledger.append(DeletionIntent.for_scope(
                            row['owner_id'], row['scope_type'], row['scope_id'], 'expiry'))
                        self._apply(entry.intent, entry.generation)
                    else:
                        with self.database.transaction(OwnerContext(row['owner_id'])) as tx:
                            repo = PgDeletionRepository(tx, row['owner_id'])
                            courses, rooms = repo.descendants(row['scope_type'], row['scope_id'])
                            entry = self.ledger.append(DeletionIntent.for_scope(
                                row['owner_id'], row['scope_type'], row['scope_id'], row['origin'],
                                members=rooms, courses=courses))
                            repo.apply(entry.intent, entry.generation, self.settle_seconds)
                    report['accepted'].append(entry.generation)
                except (InactiveOwner, DBAPIError, ScopeExpanded, ValueError) as error:
                    report['unresolved'].append(dict(generation=None, reason=type(error).__name__))
        if admit:
            self.ready = not report['unresolved']
        return report

    def expire_due(self, batch=50, rounds=20):
        """Revoke and queue assets whose own deadline has passed; the database clock decides."""
        expired = 0
        for _ in range(rounds):
            due = self.queue.expired(batch)
            for owner_id, asset_id in due:
                intent = DeletionIntent.for_scope(owner_id, 'media_asset', asset_id, 'expiry')
                try:
                    generation = self.ledger.append(intent).generation
                except LedgerUnavailable:
                    # Reads are already refused by the deadline. Revoke locally now;
                    # reconcile() completes the acceptance before any physical work.
                    generation = None
                self.queue.expire(intent, generation, self.settle_seconds)
                expired += 1
            if len(due) < batch:
                break
        return expired

    def sweep_orphans(self, *, purge_unreferenced=False, grace: timedelta | None = None, pages=1000):
        """Remove stored objects that manifests say are deleted.

        An object no manifest names is only counted unless ``purge_unreferenced`` is set
        explicitly: against a wrong or empty database that removal would be total.
        """
        grace = self.policy.orphan_grace if grace is None else grace
        cutoff = self.clock.now() - grace
        report = dict(scanned=0, revived_removed=0, unreferenced=0, unreferenced_removed=0, foreign=0, failed=0)
        after = None
        for _ in range(pages):
            page = self.store.keys(after, 500)
            for key, modified in page:
                report['scanned'] += 1
                state = self.queue.key_state(key)
                remove = state == 'deleted'
                if state == 'unknown':
                    report['unreferenced'] += 1
                    remove = purge_unreferenced and modified <= cutoff
                if not remove:
                    continue
                try:
                    self.store.erase(key)
                    report['revived_removed' if state == 'deleted' else 'unreferenced_removed'] += 1
                except ValueError:
                    report['foreign'] += 1  # not a generated media key; never touched
                except ObjectUnavailable:
                    report['failed'] += 1
            if len(page) < 500:
                break
            after = page[-1][0]
        return report


class DeletionWorker:
    """Restartable physical eraser. Every step is idempotent and lease-fenced."""
    def __init__(self, *, queue, store, policy: DeletionPolicy | None = None, worker_id: str):
        self.queue, self.store, self.policy, self.worker_id = queue, store, policy or DeletionPolicy(), worker_id

    def run_once(self):
        claim = self.queue.claim(self.worker_id, self.policy.lease_seconds)
        if claim is None:
            return False
        try:
            for asset_row, key in self.queue.inventory(claim):
                try:
                    self.store.erase(key)
                except ObjectUnavailable:
                    # Access stays revoked; the request is retried after a delay.
                    self.queue.record(claim, asset_row, 'failed', 'STORAGE_UNAVAILABLE')
                    continue
                self.queue.record(claim, asset_row, 'absent')
            self.queue.finish(claim, self.policy.backoff(claim.attempt))
        except (StaleDeletionFence, SettleWindowOpen):
            # Another worker owns the request, or the proof came too early. Nothing is
            # recorded; the lease expires and the request is claimed again.
            pass
        return True

    def drain(self, limit=1000):
        count = 0
        while count < limit and self.run_once():
            count += 1
        return count


class RestoreCoordinator:
    """Brings a restored database copy to a state that may be opened, or reports why not.

    Run in isolation, before any request is served: user entry points, mail and
    providers stay off. The steps are ordered so that nothing a user deleted or lost
    access to after the backup was taken becomes readable again.
    """
    def __init__(self, *, service: DeletionService, operator, worker: DeletionWorker):
        self.service, self.operator, self.worker = service, operator, worker

    def run(self, *, ledger_witness, revoked_owners, purge_unreferenced=False):
        """``ledger_witness`` is the ledger head (generation, entry hash) recorded outside
        the ledger store; ``revoked_owners`` are the accounts that lost access after the
        backup was taken. Both are required: the restored copy and the ledger cannot
        supply them, so the operator states them explicitly, even when the list is empty.
        The service is admitted only by the last line, after every step succeeded.
        """
        service = self.service
        service.ready = False
        revoked = list(revoked_owners)
        report = {}
        # 1. The live ledger must be readable, intact and at least as long as its witness.
        report['ledger_head'] = len(service.ledger.verify_head(ledger_witness))
        # 2. Every session, challenge and proof in the copy predates the incident, and
        #    every task it could dispatch may already have been sent: freeze them.
        report['seal'] = self.operator.seal()
        report['blocked'] = self.operator.block(revoked)
        # 3. Replay deletions and revocations recorded after the backup.
        report['reconcile'] = service.reconcile(witness=ledger_witness, admit=False)
        # 4. Deadlines are evaluated against the present, not the backup time.
        report['expired'] = service.expire_due()
        # 5. Objects whose manifests are deleted or (only on request) missing.
        report['orphans'] = service.sweep_orphans(purge_unreferenced=purge_unreferenced, grace=timedelta(0))
        # 6. Physical erasure that is already due.
        report['drained'] = self.worker.drain()
        report['audit'] = self.operator.audit()
        report['ready'] = bool(not report['reconcile']['unresolved'] and report['audit']['exposed_media'] == 0
                               and report['audit']['unaccepted_requests'] == 0
                               and report['audit']['dispatchable_tasks'] == 0
                               and report['orphans']['failed'] == 0)
        service.ready = report['ready']
        return report


def dumps(report):
    return json.dumps(report, sort_keys=True, default=str)
