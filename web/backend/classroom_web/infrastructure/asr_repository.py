"""Owner-authorized ASR window and range records on the capture unit of work."""
from uuid import UUID, uuid4

from ..domain.asr import Samples
from ..domain.auth import AuthRejected
from .billing_repository import json_value
from .capture_repository import PgCaptureRepository


class PgAsrRepository(PgCaptureRepository):
    def window(self, identity=None, *, task=None, lock=True):
        where = 'id=:id' if task is None else 'task_id=:task'
        query = f'SELECT * FROM public.asr_streams WHERE owner_id=:owner AND {where}'
        row = self.execute(query + (' FOR UPDATE' if lock else ''),
                           id=identity, task=task).mappings().one_or_none()
        if row is None:
            raise AuthRejected('NOT_FOUND', 404)
        return row

    def require_free_epoch(self, epoch_id):
        active = self.execute("""SELECT EXISTS(SELECT 1 FROM public.asr_streams
            WHERE owner_id=:owner AND capture_epoch_id=:epoch
            AND state IN ('idle','streaming','catching_up','draining'))""", epoch=epoch_id).scalar_one()
        if active:
            raise AuthRejected('LEASE_CONFLICT', 409)

    def insert_window(self, *, task_id, epoch, principal, body, source_scope, origin):
        return self.execute("""INSERT INTO public.asr_streams(
            id,owner_id,class_session_id,capture_epoch_id,task_id,originating_attempt_id,provider_task_id,
            version_id,auth_session_id,device_id,security_epoch,auth_role,capture_fence,consent_version,mode,source_scope)
            VALUES(:id,:owner,:classroom,:epoch,:task,:origin,:provider,:version,
                   :session,:device,:security,:role,:fence,:consent,:mode,cast(:scope AS jsonb))
            RETURNING *""", id=uuid4(), classroom=epoch['class_session_id'], epoch=epoch['id'], task=task_id,
            origin=origin, provider=uuid4(), version=uuid4(), session=principal.session_id, device=principal.device_id,
            security=principal.security_epoch, role=principal.role, fence=epoch['fence_token'],
            consent=body['consent_version'], mode=body['mode'], scope=json_value(source_scope)).mappings().one()

    def freeze_finish(self, stream):
        return self.execute("""UPDATE public.asr_streams
            SET requested_finish_at=coalesce(requested_finish_at,clock_timestamp()),updated_at=clock_timestamp()
            WHERE owner_id=:owner AND id=:id RETURNING *""", id=stream['id']).mappings().one()

    def sent_overlap(self, epoch_id, samples: Samples, *, ignore_attempt=None):
        return self.execute("""SELECT EXISTS(
              SELECT 1 FROM public.asr_dispatches d
              JOIN public.asr_streams s ON (s.owner_id,s.id)=(d.owner_id,d.stream_id)
              WHERE d.owner_id=:owner AND d.capture_epoch_id=:epoch
                AND d.start_sample<:last AND :first<d.end_sample
                AND (cast(:ignored AS uuid) IS NULL OR s.attempt_id IS DISTINCT FROM :ignored)
            )""", epoch=epoch_id, last=samples.end, first=samples.start, ignored=ignore_attempt).scalar_one()

    def set_state(self, stream, state, *, attempt=None, request=None):
        return self.execute("""UPDATE public.asr_streams SET state=:state,
            attempt_id=coalesce(:attempt,attempt_id), provider_request_id=coalesce(provider_request_id,:request),
            updated_at=clock_timestamp() WHERE owner_id=:owner AND id=:id RETURNING *""",
            state=state, attempt=attempt, request=request, id=stream['id']).mappings().one()

    def check_session(self, stream):
        permitted = self.execute("""SELECT classroom_web_session_valid(:owner,:session,:device,:security,:role)""",
            session=stream['auth_session_id'], device=stream['device_id'],
            security=stream['security_epoch'], role=stream['auth_role']).scalar_one()
        if not permitted:
            raise AuthRejected()

    def request_scope(self, stream):
        policy = self.scope(stream['class_session_id'])
        epoch = self.epoch(stream['capture_epoch_id'], stream['class_session_id'])
        self.consents(epoch, policy)
        self.check_session(stream)
        if not policy['synthetic'] or policy['asr_consent_version'] != stream['consent_version']:
            raise AuthRejected('CONSENT_REQUIRED', 403)
        return epoch

    def source_cutoff(self, stream, epoch):
        cutoff = stream['requested_finish_at']
        if stream['mode'] != 'live':
            return cutoff
        lease = self.lease()
        if epoch['status'] == 'recording':
            if (lease is None or lease['capture_epoch_id'] != epoch['id']
                    or lease['fence_token'] != stream['capture_fence'] or lease['device_id'] != stream['device_id']
                    or lease['released_at'] is not None or lease['expires_at'] <= self.now()):
                raise AuthRejected('STALE_FENCE', 409)
            return cutoff
        if epoch['status'] not in ('paused', 'ended', 'retired'):
            raise AuthRejected('STALE_FENCE', 409)
        if epoch['status'] == 'retired' and epoch['end_sample'] is None:
            raise AuthRejected('STALE_FENCE', 409)
        finished = epoch['wall_ended_at']
        if epoch['status'] in ('paused', 'retired'):
            finished = self.execute("""SELECT max(wall_start) FROM public.capture_gaps
                WHERE owner_id=:owner AND capture_epoch_id=:epoch AND kind='pause'
                AND reason_code='paused'""", epoch=epoch['id']).scalar_one()
        if finished is None:
            raise AuthRejected('STALE_FENCE', 409)
        return min(cutoff, finished) if cutoff is not None else finished

    def next_chunk(self, stream, epoch, cutoff):
        sent = self.execute("""SELECT coalesce(max(end_sample),-1)
            FROM public.asr_dispatches WHERE owner_id=:owner AND stream_id=:id""",
            id=stream['id']).scalar_one()
        explicit = stream['source_scope']
        selected = list(item for item in explicit if item['end'] > sent)
        if explicit and not selected:
            return None
        if selected:
            item = selected[0]
            chunk = self.chunk(UUID(item['id']))
            source, asset = self.asr_source(chunk, epoch)
            if (source.receipt_id != item['receipt'] or source.sha256 != item['sha256']
                    or (source.ingest_mode == 'recovery' and epoch['manifest_sha256'] != item['manifest'])):
                raise AuthRejected('SOURCE_UNAVAILABLE', 409)
            start, end = max(item['first'], sent), item['end']
            if sent >= 0 and item['first'] > sent:
                return None
        else:
            frontier = sent
            if frontier < 0:
                frontier = self.execute("""SELECT coalesce(max(end_sample),0) FROM public.asr_dispatches
                    WHERE owner_id=:owner AND capture_epoch_id=:epoch""", epoch=epoch['id']).scalar_one()
            identity = self.execute("""SELECT c.id FROM public.audio_chunks c JOIN public.audio_chunk_state cs
                ON (cs.owner_id,cs.chunk_id)=(c.owner_id,c.id)
                JOIN public.media_assets a ON (a.owner_id,a.id)=(c.owner_id,c.asset_id)
                WHERE c.owner_id=:owner AND c.capture_epoch_id=:epoch AND c.end_sample>:frontier
                    AND c.accepted_fence=:fence AND c.ingest_mode='live' AND cs.recovery_grant_id IS NULL
                    AND a.status='committed'
                    AND (cast(:cutoff AS timestamptz) IS NULL OR a.confirmed_at<=:cutoff)
                ORDER BY c.start_sample LIMIT 1""", epoch=epoch['id'], frontier=frontier,
                cutoff=cutoff, fence=stream['capture_fence']).scalar_one_or_none()
            if identity is None:
                return None
            chunk = self.chunk(identity)
            source, asset = self.asr_source(chunk, epoch)
            if sent >= 0 and source.start_sample > sent:
                return None
            start, end = max(source.start_sample, frontier), source.end_sample
        if cutoff is not None and asset['confirmed_at'] > cutoff:
            return None
        return source, asset, Samples(start, end)

    def add_dispatch(self, stream, frame, identity):
        samples = frame.samples
        if self.sent_overlap(stream['capture_epoch_id'], samples,
                             ignore_attempt=stream['originating_attempt_id']):
            raise AuthRejected('REVISION_CONFLICT', 409)
        self.execute("""INSERT INTO public.asr_dispatches
            (id,owner_id,stream_id,chunk_id,capture_epoch_id,start_sample,end_sample,frame_sha256)
            VALUES(:id,:owner,:stream,:chunk,:epoch,:first,:last,:sha)""", id=identity, stream=stream['id'],
            chunk=UUID(frame.source.chunk_id), epoch=stream['capture_epoch_id'], first=samples.start,
            last=samples.end, sha=frame.provider_value()['sha256'])

    def confirm_handoff(self, stream, identity):
        self.execute("""UPDATE public.asr_dispatches SET handed_off=true
            WHERE owner_id=:owner AND stream_id=:stream AND id=:id""", stream=stream['id'], id=identity)

    def record_final(self, stream, data, timeline_offset):
        samples = Samples(data['start_sample'], data['end_sample'])
        covered = self.execute("""SELECT start_sample,end_sample,chunk_id FROM public.asr_dispatches
            WHERE owner_id=:owner AND stream_id=:stream AND handed_off
            AND start_sample<:last AND end_sample>:first ORDER BY start_sample""",
            stream=stream['id'], first=samples.start, last=samples.end).mappings().all()
        cursor = samples.start
        for frame in covered:
            if frame['start_sample'] > cursor:
                return
            cursor = max(cursor, frame['end_sample'])
        if cursor < samples.end:
            return
        segment = UUID(data['segment_id'])
        chunks = list(dict.fromkeys(frame['chunk_id'] for frame in covered))
        start_ms, end_ms = samples.classroom_ms(timeline_offset)
        old = self.execute("""SELECT * FROM public.asr_final_segments WHERE owner_id=:owner
            AND stream_id=:stream AND segment_id=:segment""",
            stream=stream['id'], segment=segment).mappings().one_or_none()
        if old is not None:
            if (old['revision'], old['body'], old['start_ms'], old['end_ms'], old['end_sample']) != (
                    data['revision'], data['text'], start_ms, end_ms, samples.end):
                raise AuthRejected('REVISION_CONFLICT', 409)
            return
        identity = uuid4()
        self.execute("""INSERT INTO public.asr_final_segments(id,owner_id,stream_id,segment_id,revision,
            sequence,start_ms,end_ms,end_sample,source_chunk_ids,body,provider_request_id)
            VALUES(:id,:owner,:stream,:segment,:revision,:sequence,:start_ms,:end_ms,:sample,:chunks,:body,:request)""",
            id=identity, stream=stream['id'], segment=segment, revision=data['revision'], sequence=data['sequence'],
            start_ms=start_ms, end_ms=end_ms, sample=samples.end, chunks=chunks,
            body=data['text'], request=data['provider_request_id'])
        self.event('transcript.final', None, {'segment_revision_id': str(identity)}, stream['class_session_id'])

    def final_event(self, identity):
        row = self.execute("""SELECT f.*,s.version_id,s.class_session_id FROM public.asr_final_segments f
            JOIN public.asr_streams s ON (s.owner_id,s.id)=(f.owner_id,f.stream_id)
            WHERE f.owner_id=:owner AND f.id=:id""", id=identity).mappings().one_or_none()
        if row is None:
            return None
        return dict(class_session_id=str(row['class_session_id']), stream_id=str(row['stream_id']),
            segment_id=str(row['segment_id']), segment_revision_id=str(row['id']),
            transcript_version_id=str(row['version_id']), sequence=row['sequence'], audio_offset_ms=row['start_ms'],
            end_ms=row['end_ms'], status='final', text=row['body'], provider_request_id=row['provider_request_id'],
            revision=row['revision'], timestamp_source='provider_segment')

    def view(self, stream):
        ends = self.execute("""SELECT
            (SELECT coalesce(max(end_sample),0) FROM public.asr_dispatches
                WHERE owner_id=:owner AND stream_id=:id) AS submitted,
            (SELECT coalesce(max(end_sample),0) FROM public.asr_final_segments
                WHERE owner_id=:owner AND stream_id=:id) AS confirmed""", id=stream['id']).mappings().one()
        return {
            'id': str(stream['id']),
            'class_session_id': str(stream['class_session_id']),
            'capture_epoch_id': str(stream['capture_epoch_id']),
            'task_id': str(stream['task_id']),
            'state': stream['state'],
            'provider': 'aliyun',
            'model': 'qwen-audio-3.1-asr-flash-streaming',
            'provider_request_id': stream['provider_request_id'],
            'submitted_end_sample': ends['submitted'],
            'confirmed_end_sample': ends['confirmed'],
        }
