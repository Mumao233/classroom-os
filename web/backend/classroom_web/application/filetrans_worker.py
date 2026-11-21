"""One fenced, independently metered synthetic file job per claimed task."""
from datetime import datetime
from hashlib import sha256
from time import monotonic

from sqlalchemy import text

from ..domain.auth import AuthRejected
from ..domain.billing import BillingBlocked
from ..domain.data import Gone, InactiveOwner, NotFound, OwnerContext, RevisionConflict
from ..domain.filetrans import FilePart, FileReply, KIND, SCHEMA, SyntheticFiletrans
from ..domain.media import ObjectUnavailable
from ..domain.tasks import ResultConflict, StaleFence, UnsafeRetry
from ..infrastructure.auth_deadlines import DEADLINE
from ..infrastructure.filetrans_repository import OtherGeneration, PgFiletransRepository
from ..infrastructure.qa_repository import _json


class FiletransWorker:
    def __init__(self, service, scheduler, *, worker_id, provider=None):
        if provider is not None and type(provider) is not SyntheticFiletrans:
            raise ValueError('Filetrans requires its independent synthetic adapter')
        self.service = service
        self.queue = scheduler
        self.worker_id = worker_id
        self.provider = provider if provider is not None else SyntheticFiletrans()

    def _input(self, tx, task_id):
        self.service.require_ready(new=True)
        repo = PgFiletransRepository(tx)
        run = repo.run(task_id)
        repo.check_session(run)
        return repo, repo.get_plan(run['plan_id'])

    def _parts(self, owner, claim):
        token = DEADLINE.set(monotonic() + 600)
        try:
            with self.service.database.transaction(owner) as tx:
                repo, plan = self._input(tx, claim.id)
                sources = repo.verify(plan)
                tx.billing._switches(KIND)
            parts = []
            for chunk, obj in sources:
                self.service.require_ready(new=True)
                with self.service.database.transaction(owner) as tx:
                    repo = PgFiletransRepository(tx)
                    run = repo.run(claim.id)
                    repo.check_session(run)
                    repo.recheck_part(run['class_session_id'], chunk, obj)
                # Each new private read needs its own authorization, without
                # holding classroom or billing locks over object-store I/O.
                parts.append(self._part(chunk, obj))
            return tuple(parts)
        finally:
            DEADLINE.reset(token)

    def _part(self, chunk, obj):
        # Validate the complete immutable object but expose only the selected
        # PCM slice to the synthetic adapter via its digest and exact byte count.
        self.service.require_ready(new=True)
        # PR-05b bounds each durable PCM chunk to 32,000 bytes, so the
        # authorized object maps to exactly one pinned range read.
        if not 1 <= obj.byte_length <= 32_000:
            raise ObjectUnavailable('invalid Filetrans PCM chunk length')
        block = self.service.capture.store.read(obj, 0, obj.byte_length - 1)
        if len(block) != obj.byte_length or sha256(block).hexdigest() != chunk['sha256']:
            raise ObjectUnavailable('filetrans PCM checksum mismatch')
        start = 2 * (chunk['first'] - chunk['source_first'])
        end = 2 * (chunk['last'] - chunk['source_first'])
        selected = block[start:end]
        return FilePart(chunk, sha256(selected).hexdigest(), len(selected))

    def _authorize(self, owner, claim, parts):
        with self.service.database.transaction(owner) as tx:
            repo, plan = self._input(tx, claim.id)
            if [part.chunk for part in parts] != [chunk for chunk, _ in repo.verify(plan)]:
                raise AuthRejected('SOURCE_CHANGED', 409)
            if repo.config(self.service.price_id, plan['audio_manifest']) != plan['config']:
                raise BillingBlocked('synthetic Filetrans price changed')
            task = tx.tasks.get(claim.id)
            reservation = tx.billing.for_task(task.id)
            if (reservation is None or reservation['price_id'] != self.service.price_id
                    or reservation['maximum_units'] != plan['config']['units']):
                raise BillingBlocked('frozen Filetrans reservation required')
            # Budget lock precedes task/attempt locks, as in authorize_send.
            tx.billing._lock_existing(reservation['id'])
            attempt = tx.tasks.prepare_attempt(task.id, claim.lease_token, self.worker_id)
            self.service.lifecycle.before_submit(tx, task, attempt, claim, self.worker_id)
            expiry = min(reservation['expires_at'], claim.lease_expires_at,
                min(datetime.fromisoformat(part.chunk['expires_at']) for part in parts))
            return attempt, monotonic() + (expiry - repo.now()).total_seconds()

    def _fail_unsent(self, owner, claim):
        with self.service.database.transaction(owner) as tx:
            tx.tasks.fail_unsent(claim.id, claim.lease_token, self.worker_id, 'SOURCE_UNAVAILABLE')

    def _unknown(self, owner, claim, attempt):
        with self.service.database.transaction(owner) as tx:
            tx.tasks.mark_unknown(claim.id, attempt.id, claim.lease_token, self.worker_id)

    def _publish(self, owner, claim, attempt, reply):
        with self.service.database.transaction(owner) as tx:
            self.service.require_ready()
            repo = PgFiletransRepository(tx)
            run = repo.run(claim.id)
            repo.check_session(run)
            plan = repo.get_plan(run['plan_id'])
            repo.publication(plan)
            task = tx.tasks._fenced(claim.id, claim.lease_token, self.worker_id)
            tx.connection.execute(text("""UPDATE task_attempts SET provider_request_id=:request
                WHERE owner_id=:owner AND task_id=:task AND id=:attempt"""),
                dict(request=reply.request_id, owner=claim.owner_id, task=task.id, attempt=attempt.id))
            if reply.text is None:
                tx.tasks.fail_asr_attempt(task.id, attempt.id, claim.lease_token, self.worker_id)
            else:
                tx.tasks.store_result(task.id, attempt.id, _json({'text': reply.text, 'request_id': reply.request_id}))
                repo.publish(task, plan, reply)
                tx.tasks.publish_result(task.id, attempt.id, claim.lease_token, self.worker_id)

    def recover_expired(self):
        for claim in self.queue.expired():
            if claim.kind != KIND:
                continue
            try:
                with self.service.database.transaction(OwnerContext(claim.owner_id)) as tx:
                    task = tx.tasks.get(claim.id)
                    if task.schema_version == SCHEMA:
                        tx.tasks.recover(task.id, claim.lease_token)
            except (Gone, InactiveOwner, NotFound):
                self.queue.recover_revoked(claim.id, claim.lease_token)
            except (StaleFence, RevisionConflict):
                continue
            finally:
                self.service.lifecycle.synchronize(claim.owner_id, claim.id)

    def run_once(self):
        try:
            self.service.require_ready()
        except AuthRejected:
            return False
        self.recover_expired()
        claims = self.queue.claim(self.worker_id, lease_seconds=660, schema_version=SCHEMA)
        if not claims:
            return False
        claim = claims[0]
        owner = OwnerContext(claim.owner_id)
        try:
            try:
                parts = self._parts(owner, claim)
                attempt, send_deadline = self._authorize(owner, claim, parts)
            except (AuthRejected, ObjectUnavailable, BillingBlocked):
                self._fail_unsent(owner, claim)
                return True
            try:
                self.service.require_ready(new=True)
                if monotonic() >= send_deadline:
                    raise TimeoutError('committed Filetrans intent expired before handoff')
                reply = self.provider.submit(attempt.id, parts)
                if not isinstance(reply, FileReply):
                    raise ValueError('invalid Filetrans reply')
            except (AuthRejected, ConnectionError, TimeoutError, ValueError):
                self._unknown(owner, claim, attempt)
                return True
            try:
                self._publish(owner, claim, attempt, reply)
            except AuthRejected:
                self._unknown(owner, claim, attempt)
            finally:
                self.service.lifecycle.completed(claim.owner_id, claim.id, reply.usage)
        except (Gone, InactiveOwner, NotFound, OtherGeneration, StaleFence,
                RevisionConflict, UnsafeRetry, ResultConflict):
            # A missed publication never becomes a second send. Lease recovery
            # keeps committed intent and billing evidence on the first attempt.
            pass
        finally:
            self.service.lifecycle.synchronize(claim.owner_id, claim.id)
        return True

    async def serve(self, stop, errors):
        import asyncio

        while not stop.is_set():
            try:
                worked = await asyncio.to_thread(self.run_once)
            except Exception as error:
                errors.append({'kind': type(error).__name__, 'stage': 'filetrans_worker'})
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.25)
                except TimeoutError:
                    pass
