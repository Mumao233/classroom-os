"""Default-off capture authority. Object bytes and durable facts precede acknowledgement."""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import secrets
from uuid import uuid4

import sqlalchemy as sa

from ..domain.auth import AuthRejected
from ..domain.capture import CaptureLeaseConflict, MAX_CHUNK_BYTES, missing_ranges, sequence_ranges, validate_chunk
from ..domain.data import Gone, NotFound
from ..domain.identity import AuthorizationDenied
from ..domain.media import ObjectUnavailable
from ..domain.tasks import IdempotencyMismatch
from ..infrastructure.capture_repository import PgCaptureRepository
from .media import fingerprint, identifier


def lease_dto(lease, epoch, policy):
    return dict(id=str(lease['id']), class_session_id=str(lease['class_session_id']),
        capture_epoch_id=str(lease['capture_epoch_id']), fence_token=lease['fence_token'],
        expires_at=lease['expires_at'].isoformat(), renew_after_seconds=max(1, policy['lease_seconds'] // 2),
        timeline_offset_ms=epoch['timeline_offset_ms'], sample_rate=16000, channel_count=1, encoding='pcm_s16le')


def chunk_dto(chunk, asset, asr_submission='not_submitted'):
    durable = asset['status'] == 'committed'
    return dict(id=str(chunk['id']), descriptor=dict(class_session_id=str(chunk['class_session_id']),
        capture_epoch_id=str(chunk['capture_epoch_id']), seq=chunk['seq'], start_sample=chunk['start_sample'],
        end_sample=chunk['end_sample'], byte_length=asset['size_bytes'], sha256=chunk['sha256'],
        encoding='pcm_s16le', sample_rate=16000, channels=1), state='durable' if durable else 'pending',
        durable_at=asset['confirmed_at'].isoformat() if durable else None,
        asr_submission=asr_submission, receipt_id=str(chunk['receipt_id']))


class CaptureService:
    def __init__(self, *, auth, database, store, deletion=None, enabled=False):
        self.auth, self.database, self.store = auth, database, store
        self.deletion, self.enabled = deletion, enabled
        store.validate_private()

    def available(self):
        if not self.enabled or self.deletion is None or not self.deletion.ready:
            raise AuthRejected('SERVICE_DISABLED', 503)

    @contextmanager
    def transaction(self, context, classroom, *, write=True):
        phase = 'executing'
        try:
            self.available()
            if write:
                self.auth.require_csrf(context, 'session')
            principal = self.auth.principal(context)
            with self.database.authorized_transaction(principal, activity=True) as tx:
                repo = PgCaptureRepository(tx, principal.user_id)
                policy = repo.scope(identifier(classroom))
                yield repo, principal, policy
                self.available()
                phase = 'committing'
        except AuthorizationDenied:
            raise AuthRejected() from None
        except NotFound:
            raise AuthRejected('NOT_FOUND', 404) from None
        except Gone:
            raise AuthRejected('GONE', 410) from None
        except IdempotencyMismatch:
            raise AuthRejected('IDEMPOTENCY_MISMATCH', 409) from None
        except ObjectUnavailable:
            raise AuthRejected('STORAGE_UNAVAILABLE', 503) from None
        except sa.exc.DBAPIError as error:
            state = getattr(error.orig, 'sqlstate', None)
            if phase == 'committing' and (error.connection_invalidated or state is None or state.startswith('08')):
                raise AuthRejected('RESULT_UNKNOWN', 503) from None
            raise

    def active(self, repo, principal, epoch, fence, *, recording=False):
        lease = repo.lease()
        if (lease is None or lease['capture_epoch_id'] != epoch['id'] or lease['fence_token'] != fence
                or epoch['fence_token'] != fence or lease['device_id'] != principal.device_id
                or lease['released_at'] is not None or lease['expires_at'] <= repo.now()
                or epoch['status'] not in ('recording', 'paused')):
            raise AuthRejected('STALE_FENCE', 409)
        if recording and epoch['status'] != 'recording':
            raise AuthRejected('LEASE_CONFLICT', 409)
        return lease

    def acquire(self, context, classroom, key, body):
        with self.transaction(context, classroom) as (repo, principal, policy):
            return self.acquire_in_transaction(repo, principal, policy, classroom, key, body)

    def acquire_in_transaction(self, repo, principal, policy, classroom, key, body):
        if identifier(body['device_id']) != principal.device_id:
            raise AuthRejected('FORBIDDEN', 403)
        for kind in ('microphone', 'recovery', 'asr'):
            if body[kind + '_consent_version'] != policy[kind + '_consent_version']:
                raise AuthRejected('CONSENT_REQUIRED', 403)
        digest = fingerprint(dict(class_session_id=classroom, **body))
        receipt = repo.receipt('acquireCapture', identifier(key), digest)
        current = repo.lease()
        if receipt:
            epoch = repo.epoch(identifier(receipt['response_body']['capture_epoch_id']), identifier(classroom))
            repo.consents(epoch, policy)
            lease = self.active(repo, principal, epoch, epoch['fence_token'])
            return lease_dto(lease, epoch, policy)
        now = repo.now()
        if current and current['released_at'] is None:
            if not body['confirm_takeover'] or body.get('expected_lease_id') != str(current['id']):
                raise CaptureLeaseConflict(current['id'])
        elif body.get('expected_lease_id') is not None and (current is None or body['expected_lease_id'] != str(current['id'])):
            raise CaptureLeaseConflict(current['id'] if current else None)
        fence = current['fence_token'] + 1 if current else 1
        lost_event = None
        if current and current['released_at'] is None:
            repo.execute("""UPDATE capture_epochs SET status='retired',wall_ended_at=:now
                WHERE owner_id=:owner AND id=:id""", id=current['capture_epoch_id'], now=now)
            retired = dict(id=current['capture_epoch_id'], class_session_id=current['class_session_id'])
            repo.gap(retired, 'unknown', 'takeover', wall_end=now)
            lost_event = dict(class_session_id=str(current['class_session_id']), capture_epoch_id=str(current['capture_epoch_id']),
                              fence_token=current['fence_token'], reason='takeover')
        epoch_id, lease_id = uuid4(), uuid4()
        timeline = repo.execute("""SELECT coalesce(max(e.timeline_offset_ms+(greatest(coalesce(e.end_sample,0),s.last_sample)+15)/16),0)
            FROM capture_epochs e JOIN capture_epoch_state s ON s.epoch_id=e.id
            WHERE e.owner_id=:owner AND e.class_session_id=:classroom""", classroom=identifier(classroom)).scalar_one()
        repo.execute("""INSERT INTO capture_epochs(id,owner_id,class_session_id,device_id,fence_token,
            timeline_offset_ms,wall_started_at,status) VALUES(:id,:owner,:classroom,:device,:fence,:timeline,:now,'recording')""",
            id=epoch_id, classroom=identifier(classroom), device=principal.device_id, fence=fence, timeline=timeline, now=now)
        consent_ids = {}
        for kind in ('microphone', 'recovery', 'asr'):
            consent_ids[kind] = uuid4()
            repo.execute("""INSERT INTO consent_records(id,owner_id,kind,class_session_id,device_id,policy_version,scope,accepted_at)
                VALUES(:id,:owner,:kind,:classroom,:device,:version,CAST(:scope AS jsonb),:now)""",
                id=consent_ids[kind], kind=kind, classroom=identifier(classroom), device=principal.device_id,
                version=policy[kind + '_consent_version'], scope=json.dumps({'capture_epoch_id':str(epoch_id)}), now=now)
        repo.execute("""INSERT INTO capture_epoch_state(epoch_id,owner_id,microphone_consent_id,recovery_consent_id,
            asr_consent_id,hard_expire_at,retention_policy_version)
            VALUES(:id,:owner,:microphone,:recovery,:asr,:expiry,:retention)""", id=epoch_id, **consent_ids,
            expiry=now + policy['max_lifetime'], retention=policy['retention_policy_version'])
        expires = min(now + timedelta(seconds=policy['lease_seconds']), principal.expires_at)
        repo.execute("""INSERT INTO capture_leases(id,owner_id,class_session_id,device_id,capture_epoch_id,
            fence_token,expires_at,renewed_at) VALUES(:id,:owner,:classroom,:device,:epoch,:fence,:expires,:now)
            ON CONFLICT(owner_id) DO UPDATE SET id=EXCLUDED.id,class_session_id=EXCLUDED.class_session_id,
            device_id=EXCLUDED.device_id,capture_epoch_id=EXCLUDED.capture_epoch_id,fence_token=EXCLUDED.fence_token,
            expires_at=EXCLUDED.expires_at,renewed_at=EXCLUDED.renewed_at,released_at=NULL""",
            id=lease_id, classroom=identifier(classroom), device=principal.device_id, epoch=epoch_id, fence=fence, expires=expires, now=now)
        repo.remember('acquireCapture', identifier(key), digest, 'class_session', identifier(classroom),
                      {'capture_epoch_id':str(epoch_id)}, 201)
        if lost_event:
            repo.event('capture.lease_lost', retired, lost_event, current['class_session_id'])
        return lease_dto(repo.lease(), repo.epoch(epoch_id, identifier(classroom)), policy)

    def command(self, context, classroom, key, body, operation):
        with self.transaction(context, classroom) as (repo, principal, policy):
            epoch = repo.epoch(identifier(body['capture_epoch_id']), identifier(classroom))
            repo.consents(epoch, policy)
            digest = fingerprint(dict(class_session_id=classroom, **body))
            receipt = repo.receipt(operation, identifier(key), digest)
            if operation == 'resumeCapture' and receipt:
                resumed = repo.epoch(identifier(receipt['response_body']['capture_epoch_id']), identifier(classroom))
                repo.consents(resumed, policy)
                return lease_dto(self.active(repo,principal,resumed,resumed['fence_token']),resumed,policy)
            if operation == 'stopCapture' and receipt:
                if principal.device_id != epoch['device_id'] or body['fence_token'] != epoch['fence_token']:
                    raise AuthRejected('STALE_FENCE', 409)
                return self.manifest_value(repo, epoch)
            lease = self.active(repo, principal, epoch, body['fence_token'])
            if receipt:
                return lease_dto(lease, epoch, policy)
            if (body['last_sample'] < epoch['last_sample'] or body['last_local_seq'] < epoch['last_local_seq']
                    or body['client_monotonic_ms'] < epoch['last_monotonic_ms']
                    or body['last_sample'] > policy['max_epoch_samples'] or body['last_local_seq'] >= policy['max_chunks']):
                raise AuthRejected('VALIDATION_FAILED', 422)
            observed = repo.execute("""SELECT coalesce(max(seq),0) AS seq,coalesce(max(end_sample),0) AS sample
                FROM audio_chunks WHERE owner_id=:owner AND capture_epoch_id=:id""", id=epoch['id']).mappings().one()
            if body['last_sample'] < observed['sample'] or body['last_local_seq'] < observed['seq']:
                raise AuthRejected('VALIDATION_FAILED', 422)
            now = repo.now()
            repo.execute("""UPDATE capture_epoch_state SET last_local_seq=:seq,last_sample=:sample,last_monotonic_ms=:mono
                WHERE owner_id=:owner AND epoch_id=:id""", id=epoch['id'], seq=body['last_local_seq'],
                sample=body['last_sample'], mono=body['client_monotonic_ms'])
            if operation == 'pauseCapture':
                if epoch['status'] != 'recording':
                    raise AuthRejected('LEASE_CONFLICT', 409)
                repo.execute("UPDATE capture_epochs SET status='paused' WHERE owner_id=:owner AND id=:id", id=epoch['id'])
                repo.gap(epoch, 'pause', 'paused', start=body['last_sample'], wall_start=now)
            elif operation == 'resumeCapture':
                if epoch['status'] != 'paused':
                    raise AuthRejected('LEASE_CONFLICT', 409)
                repo.execute("""UPDATE capture_epochs SET status='retired',wall_ended_at=:now,last_expected_seq=:seq,end_sample=:sample
                    WHERE owner_id=:owner AND id=:id""", id=epoch['id'], now=now,
                    seq=body['last_local_seq'] if body['last_sample'] else None, sample=body['last_sample'])
                repo.execute("""UPDATE capture_gaps SET wall_end=:now WHERE owner_id=:owner AND capture_epoch_id=:id
                    AND kind='pause' AND wall_end IS NULL""", id=epoch['id'], now=now)
                repo.execute('UPDATE capture_leases SET released_at=:now WHERE owner_id=:owner', now=now)
                acquisition = dict(device_id=str(principal.device_id), confirm_takeover=False, expected_lease_id=str(lease['id']),
                    **{kind+'_consent_version':policy[kind+'_consent_version'] for kind in ('microphone','recovery','asr')})
                result = self.acquire_in_transaction(repo,principal,policy,classroom,str(uuid4()),acquisition)
                repo.remember(operation,identifier(key),digest,'class_session',identifier(classroom),
                              {'capture_epoch_id':result['capture_epoch_id']},200)
                return result
            elif operation == 'stopCapture':
                for gap in body['known_gaps']:
                    if not 0 <= gap['start_sample'] <= gap['end_sample'] <= body['last_sample']:
                        raise AuthRejected('VALIDATION_FAILED', 422)
                    repo.gap(epoch, 'pause' if gap['reason'] == 'paused' else 'known_missing', gap['reason'],
                             start=gap['start_sample'], end=gap['end_sample'])
                repo.execute("""UPDATE capture_epochs SET status='ended',wall_ended_at=:now,last_expected_seq=:seq,end_sample=:sample
                    WHERE owner_id=:owner AND id=:id""", id=epoch['id'], now=now,
                    seq=body['last_local_seq'] if body['last_sample'] else None, sample=body['last_sample'])
                repo.execute('UPDATE capture_epoch_state SET manifest_sha256=:sha WHERE owner_id=:owner AND epoch_id=:id',
                             id=epoch['id'], sha=body['manifest_sha256'])
                repo.execute('UPDATE capture_leases SET released_at=:now WHERE owner_id=:owner', now=now)
            elif operation != 'renewCapture':
                raise ValueError('unsupported capture operation')
            if operation != 'stopCapture':
                repo.execute('UPDATE capture_leases SET expires_at=:expires,renewed_at=:now WHERE owner_id=:owner',
                             expires=min(now + timedelta(seconds=policy['lease_seconds']), principal.expires_at, epoch['hard_expire_at']), now=now)
            repo.remember(operation, identifier(key), digest, 'class_session', identifier(classroom),
                          {'capture_epoch_id':str(epoch['id'])}, 200)
            epoch = repo.epoch(epoch['id'], identifier(classroom))
            return self.manifest_value(repo, epoch) if operation == 'stopCapture' else lease_dto(repo.lease(), epoch, policy)

    def manifest_value(self, repo, epoch):
        rows = repo.execute("""SELECT c.seq FROM audio_chunks c JOIN media_assets a ON (a.owner_id,a.id)=(c.owner_id,c.asset_id)
            WHERE c.owner_id=:owner AND c.capture_epoch_id=:epoch AND a.status='committed'
            AND a.access_revoked_at IS NULL AND a.deleted_at IS NULL AND a.expires_at>clock_timestamp()
            AND a.hard_expire_at>clock_timestamp() ORDER BY c.seq""", epoch=epoch['id']).scalars().all()
        last = epoch['last_expected_seq']
        known = repo.execute('SELECT max(seq) FROM audio_chunks WHERE owner_id=:owner AND capture_epoch_id=:id', id=epoch['id']).scalar_one()
        return dict(class_session_id=str(epoch['class_session_id']), capture_epoch_id=str(epoch['id']),
            sealed=epoch['manifest_sha256'] is not None, expected_last_seq=last,
            durable_ranges=sequence_ranges(rows), missing_ranges=missing_ranges(rows, last if last is not None else known),
            unknown_provider_ranges=sequence_ranges(repo.unknown_asr_sequences(epoch['id'])),
            audio_expires_at=epoch['hard_expire_at'].isoformat())

    def manifest(self, context, classroom, epoch_id):
        with self.transaction(context, classroom, write=False) as (repo, _, policy):
            epoch = repo.epoch(identifier(epoch_id), identifier(classroom))
            repo.consents(epoch, policy)
            return self.manifest_value(repo, epoch)

    def recovery(self, context, classroom, key, body):
        with self.transaction(context, classroom) as (repo, principal, policy):
            epoch = repo.epoch(identifier(body['capture_epoch_id']), identifier(classroom))
            repo.consents(epoch, policy)
            if principal.device_id != epoch['device_id']:
                raise AuthRejected('FORBIDDEN', 403)
            lease = repo.lease()
            if (not body['confirm_retired_epoch'] or (lease and lease['capture_epoch_id'] == epoch['id']
                    and lease['released_at'] is None and lease['expires_at'] > repo.now())):
                raise AuthRejected('LEASE_CONFLICT', 409)
            if (not 0 < body['known_last_sample'] <= policy['max_epoch_samples']
                    or not 0 <= body['known_last_seq'] < policy['max_chunks']):
                raise AuthRejected('VALIDATION_FAILED', 422)
            observed = repo.execute("""SELECT coalesce(max(seq),0) AS seq,coalesce(max(end_sample),0) AS sample
                FROM audio_chunks WHERE owner_id=:owner AND capture_epoch_id=:id""", id=epoch['id']).mappings().one()
            if body['known_last_sample'] < max(observed['sample'], epoch['last_sample']) or body['known_last_seq'] < max(observed['seq'], epoch['last_local_seq']):
                raise AuthRejected('VALIDATION_FAILED', 422)
            if epoch['manifest_sha256'] is not None and (epoch['manifest_sha256'] != body['manifest_sha256']
                    or epoch['last_expected_seq'] != body['known_last_seq'] or epoch['end_sample'] != body['known_last_sample']):
                raise AuthRejected('IDEMPOTENCY_MISMATCH', 409)
            digest = fingerprint(dict(class_session_id=classroom, **body))
            receipt = repo.receipt('authorizeRecovery', identifier(key), digest)
            if epoch['manifest_sha256'] is None:
                repo.execute("""UPDATE capture_epochs SET status='retired',wall_ended_at=coalesce(wall_ended_at,clock_timestamp()),
                    last_expected_seq=:seq,end_sample=:sample WHERE owner_id=:owner AND id=:id""",
                    id=epoch['id'], seq=body['known_last_seq'], sample=body['known_last_sample'])
                repo.execute('UPDATE capture_epoch_state SET manifest_sha256=:sha WHERE owner_id=:owner AND epoch_id=:id',
                             id=epoch['id'], sha=body['manifest_sha256'])
                repo.gap(epoch, 'unknown', 'offline_recovery')
            if receipt is None:
                repo.remember('authorizeRecovery', identifier(key), digest, 'class_session', identifier(classroom),
                              {'capture_epoch_id':str(epoch['id'])}, 200)
            token = secrets.token_urlsafe(32)
            expiry = min(repo.now() + timedelta(seconds=policy['recovery_seconds']), principal.expires_at, epoch['hard_expire_at'])
            repo.execute("""INSERT INTO capture_recovery_grants(id,owner_id,epoch_id,session_id,device_id,security_epoch,
                token_hash,manifest_sha256,last_seq,last_sample,expires_at)
                VALUES(:id,:owner,:epoch,:session,:device,:security,:hash,:manifest,:seq,:sample,:expires)""",
                id=uuid4(), epoch=epoch['id'], session=principal.session_id, device=principal.device_id,
                security=principal.security_epoch, hash=hashlib.sha256(token.encode()).hexdigest(), manifest=body['manifest_sha256'],
                seq=body['known_last_seq'], sample=body['known_last_sample'], expires=expiry)
            return dict(manifest=self.manifest_value(repo, repo.epoch(epoch['id'], identifier(classroom))),
                        recovery_upload_token=token, expires_at=expiry.isoformat(), asr_authorized=False)

    def grant(self, repo, principal, epoch, *, token=None, grant_id=None):
        if token is not None:
            if not isinstance(token, str) or len(token) != 43:
                raise AuthRejected('FORBIDDEN', 403)
            row = repo.execute('SELECT * FROM capture_recovery_grants WHERE owner_id=:owner AND token_hash=:hash',
                               hash=hashlib.sha256(token.encode()).hexdigest()).mappings().one_or_none()
        else:
            row = repo.execute('SELECT * FROM capture_recovery_grants WHERE owner_id=:owner AND id=:id',
                               id=grant_id).mappings().one_or_none()
        if (row is None or row['epoch_id'] != epoch['id'] or row['session_id'] != principal.session_id
                or row['device_id'] != principal.device_id or row['security_epoch'] != principal.security_epoch
                or row['expires_at'] <= repo.now() or row['manifest_sha256'] != epoch['manifest_sha256']):
            raise AuthRejected('FORBIDDEN', 403)
        return row

    def prepare(self, context, classroom, key, body, recovery_token=None, requested_chunk_id=None):
        with self.transaction(context, classroom) as (repo, principal, policy):
            validate_chunk(body, max_samples=policy['max_epoch_samples'], max_chunks=policy['max_chunks'])
            epoch = repo.epoch(identifier(body['capture_epoch_id']), identifier(classroom))
            repo.consents(epoch, policy)
            grant = None
            if recovery_token is None:
                self.active(repo, principal, epoch, body['fence_token'], recording=True)
            else:
                grant = self.grant(repo, principal, epoch, token=recovery_token)
                if body['fence_token'] != epoch['fence_token'] or body['seq'] > grant['last_seq'] or body['end_sample'] > grant['last_sample']:
                    raise AuthRejected('STALE_FENCE', 409)
            digest = fingerprint(dict(class_session_id=classroom, **body))
            receipt = repo.receipt('prepareAudioChunk', identifier(key), digest)
            existing = repo.execute('SELECT id FROM audio_chunks WHERE owner_id=:owner AND capture_epoch_id=:epoch AND seq=:seq',
                                    epoch=epoch['id'], seq=body['seq']).scalar_one_or_none()
            if existing:
                chunk = repo.chunk(existing)
                if any(chunk[field] != body[field] for field in ('start_sample','end_sample','sha256')):
                    raise AuthRejected('IDEMPOTENCY_MISMATCH', 409)
                if grant:
                    repo.execute('UPDATE audio_chunk_state SET recovery_grant_id=:grant WHERE owner_id=:owner AND chunk_id=:id',
                                 grant=grant['id'], id=existing)
                if receipt is None:
                    repo.remember('prepareAudioChunk', identifier(key), digest, 'media_asset', chunk['asset_id'],
                                  {'chunk_id':str(existing)}, 201)
                return chunk_dto(repo.chunk(existing), repo.asset(chunk['asset_id']), repo.asr_submission(existing))
            if receipt:
                raise AuthRejected('RESULT_UNKNOWN', 503)
            conflict = repo.execute("""SELECT EXISTS(SELECT FROM audio_chunks WHERE owner_id=:owner AND capture_epoch_id=:epoch
                AND ((seq<:seq AND end_sample>:start) OR (seq>:seq AND start_sample<:end)))""",
                epoch=epoch['id'], seq=body['seq'], start=body['start_sample'], end=body['end_sample']).scalar_one()
            if conflict:
                raise AuthRejected('IDEMPOTENCY_MISMATCH', 409)
            chunk_id = identifier(requested_chunk_id) if requested_chunk_id is not None else uuid4()
            asset_id, receipt_id = uuid4(), uuid4()
            repo.execute("""INSERT INTO media_assets(id,owner_id,class_session_id,kind,sha256,size_bytes,mime_type,object_key,expires_at,hard_expire_at)
                VALUES(:id,:owner,:classroom,'audio_chunk',:sha,:length,'audio/pcm',:object,:expiry,:expiry)""",
                id=asset_id, classroom=identifier(classroom), sha=body['sha256'], length=body['byte_length'],
                object=f'media/{principal.user_id.hex}/{chunk_id.hex}/{body["sha256"]}', expiry=epoch['hard_expire_at'])
            repo.execute("""INSERT INTO audio_chunks(id,owner_id,class_session_id,capture_epoch_id,asset_id,seq,
                start_sample,end_sample,sha256,ingest_mode,accepted_fence)
                VALUES(:id,:owner,:classroom,:epoch,:asset,:seq,:start,:end,:sha,:mode,:fence)""",
                id=chunk_id, classroom=identifier(classroom), epoch=epoch['id'], asset=asset_id, seq=body['seq'],
                start=body['start_sample'], end=body['end_sample'], sha=body['sha256'],
                mode='recovery' if grant else 'live', fence=epoch['fence_token'])
            repo.execute("""INSERT INTO audio_chunk_state(chunk_id,owner_id,receipt_id,recovery_grant_id)
                VALUES(:id,:owner,:receipt,:grant)""", id=chunk_id, receipt=receipt_id, grant=grant['id'] if grant else None)
            repo.remember('prepareAudioChunk', identifier(key), digest, 'media_asset', asset_id, {'chunk_id':str(chunk_id)}, 201)
            return chunk_dto(repo.chunk(chunk_id), repo.asset(asset_id))

    def locate(self, context, chunk_id):
        self.available()
        principal = self.auth.principal(context)
        with self.database.authorized_transaction(principal) as tx:
            row = tx.connection.execute(sa.text('SELECT class_session_id FROM audio_chunks WHERE owner_id=:owner AND id=:id'),
                dict(owner=principal.user_id, id=identifier(chunk_id))).scalar_one_or_none()
            if row is None:
                raise AuthRejected('NOT_FOUND', 404)
            return str(row)

    def upload_authority(self, repo, principal, chunk, epoch):
        if chunk['recovery_grant_id'] is not None:
            grant = self.grant(repo, principal, epoch, grant_id=chunk['recovery_grant_id'])
            if chunk['seq'] > grant['last_seq'] or chunk['end_sample'] > grant['last_sample']:
                raise AuthRejected('FORBIDDEN', 403)
        else:
            self.active(repo, principal, epoch, chunk['accepted_fence'], recording=True)

    def put(self, context, chunk_id, key, content):
        classroom = self.locate(context, chunk_id)
        with self.transaction(context, classroom) as (repo, principal, policy):
            # Resolve immutable epoch before taking chunk/media locks.
            epoch_id = repo.execute('SELECT capture_epoch_id FROM audio_chunks WHERE owner_id=:owner AND id=:id', id=identifier(chunk_id)).scalar_one()
            epoch = repo.epoch(epoch_id, identifier(classroom))
            repo.consents(epoch, policy)
            chunk = repo.chunk(identifier(chunk_id))
            self.upload_authority(repo, principal, chunk, epoch)
            asset = repo.asset(chunk['asset_id'])
            if len(content) != asset['size_bytes'] or hashlib.sha256(content).hexdigest() != chunk['sha256']:
                raise AuthRejected('VALIDATION_FAILED', 422)
            digest = fingerprint(dict(chunk_id=chunk_id, sha256=chunk['sha256']))
            receipt = repo.receipt('putAudioChunk', identifier(key), digest)
            if asset['status'] != 'committed':
                import io
                obj = self.store.put(asset['object_key'], io.BytesIO(content), len(content), chunk['sha256'])
                # Conditional object creation keeps the first object timestamp after a DB rollback.
                received = obj.received_at
                now = repo.now()
                if epoch['hard_expire_at'] <= now:
                    raise AuthRejected('GONE', 410)
                self.upload_authority(repo, principal, chunk, epoch)
                repo.execute("""UPDATE media_assets SET status='committed',object_etag=:etag,object_version=:version,
                    first_cloud_received_at=:received,retention_anchor_at=:received,expires_at=:expiry,hard_expire_at=:expiry,
                    retention_policy_version=:retention,confirmed_at=:now,revision=revision+1,updated_at=:now
                    WHERE owner_id=:owner AND id=:id""", id=asset['id'], etag=obj.etag, version=obj.version_id,
                    received=received, expiry=min(epoch['hard_expire_at'],received+policy['max_lifetime']),
                    retention=epoch['retention_policy_version'], now=now)
                result = chunk_dto(chunk, repo.asset(asset['id'], available=True))
                data = dict(class_session_id=classroom, capture_epoch_id=str(epoch['id']), chunk_id=chunk_id,
                    seq=chunk['seq'], sha256=chunk['sha256'], end_sample=chunk['end_sample'],
                    receipt_id=result['receipt_id'], durable_at=result['durable_at'])
                repo.event('audio.chunk_durable', epoch, data, identifier(classroom))
            if receipt is None:
                repo.remember('putAudioChunk', identifier(key), digest, 'media_asset', asset['id'], {'chunk_id':chunk_id}, 200)
            return chunk_dto(chunk, repo.asset(asset['id'], available=True), repo.asr_submission(chunk['id']))

    def durable_source(self, context, chunk_id):
        classroom = self.locate(context, chunk_id)
        with self.transaction(context, classroom, write=False) as (repo, _, policy):
            epoch_id = repo.execute('SELECT capture_epoch_id FROM audio_chunks WHERE owner_id=:owner AND id=:id', id=identifier(chunk_id)).scalar_one()
            epoch = repo.epoch(epoch_id, identifier(classroom))
            repo.consents(epoch, policy)
            chunk = repo.chunk(identifier(chunk_id))
            source, _ = repo.asr_source(chunk, epoch)
            return source

    def ws_scope(self, context, scope):
        with self.transaction(context, scope['class_session_id'], write=False) as (repo,principal,policy):
            epoch=repo.epoch(identifier(scope['capture_epoch_id']),identifier(scope['class_session_id']))
            repo.consents(epoch,policy)
            self.active(repo,principal,epoch,scope['fence_token'],recording=True)

    def issue_ws_ticket(self, context, body):
        self.ws_scope(context,body)
        return self.auth.ws_ticket(context,body)

    def durable_event(self, context, chunk_id):
        classroom=self.locate(context,chunk_id)
        with self.transaction(context,classroom,write=False) as (repo,_,policy):
            epoch_id=repo.execute('SELECT capture_epoch_id FROM audio_chunks WHERE owner_id=:owner AND id=:id',id=identifier(chunk_id)).scalar_one()
            epoch=repo.epoch(epoch_id,identifier(classroom))
            repo.consents(epoch,policy)
            chunk=repo.chunk(identifier(chunk_id));repo.asset(chunk['asset_id'],available=True)
            event=repo.execute("""SELECT * FROM outbox_events WHERE owner_id=:owner
                AND event_type='audio.chunk_durable' AND payload->>'chunk_id'=:chunk
                ORDER BY event_sequence LIMIT 1""",chunk=chunk_id).mappings().one()
            return dict(protocol_version=1,type='audio.chunk_durable',sent_at=event['created_at'].isoformat(),
                data=event['payload'],event_id=str(event['id']),cursor=repo.tx.events._cursor(event['event_sequence']))


class CaptureRecordingAuthorizer:
    """05a recording-key seam, called inside its already authorized transaction."""
    def __init__(self, capture):
        self.capture = capture

    def authorize(self, connection, principal, classroom):
        self.capture.available()
        from ..infrastructure.repositories import PgCatalogRepository
        from types import SimpleNamespace
        tx = SimpleNamespace(connection=connection, catalog=PgCatalogRepository(connection, principal.user_id))
        repo = PgCaptureRepository(tx, principal.user_id)
        policy = repo.scope(classroom)
        rows = repo.execute("""SELECT e.id FROM capture_epochs e JOIN capture_epoch_state s ON s.epoch_id=e.id
            WHERE e.owner_id=:owner AND e.class_session_id=:classroom AND e.device_id=:device
            AND s.hard_expire_at>clock_timestamp() ORDER BY e.created_at DESC LIMIT 1""",
            classroom=classroom, device=principal.device_id).scalars().all()
        if not rows:
            raise AuthRejected('CONSENT_REQUIRED', 403)
        repo.consents(repo.epoch(rows[0], classroom), policy)
