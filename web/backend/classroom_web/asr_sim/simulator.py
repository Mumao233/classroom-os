"""Bounded in-memory evidence model, deliberately not a durable adapter."""
from copy import deepcopy
from hashlib import sha256
import struct
from uuid import UUID, uuid5, NAMESPACE_URL


class ProtocolError(ValueError):
    pass


def integer(value, minimum=0, maximum=2**53 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProtocolError('invalid integer')
    return value


def uid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ProtocolError('canonical UUID required')
    return value


def identity(*parts):
    return str(uuid5(NAMESPACE_URL, repr(parts)))


def _wave_container(pcm):
    """Recognize complete WAVE structure, never infer a container from magic.

    This bounded sniff is not a decoder or proof of the declared raw format.
    RIFF/RIFX chunk sizes and RF64 ds64 lengths must describe all supplied bytes.
    """
    magic = pcm[:4]
    if len(pcm) < 44 or magic not in (b'RIFF', b'RIFX', b'RF64') or pcm[8:12] != b'WAVE':
        return False
    order = '>' if magic == b'RIFX' else '<'
    def number(offset, size=4):
        return int.from_bytes(pcm[offset:offset + size], 'big' if order == '>' else 'little')
    position, extended, data_size, sample_count = 12, {}, None, None
    if magic == b'RF64':
        if number(4) != 0xffffffff or pcm[12:16] != b'ds64':
            return False
        size = number(16)
        if size < 28 or 20 + size > len(pcm) or size != 28 + 12 * number(44):
            return False
        if number(20, 8) != len(pcm) - 8:
            return False
        data_size, sample_count = number(28, 8), number(36, 8)
        for offset in range(48, 20 + size, 12):
            extended.setdefault(pcm[offset:offset + 4], []).append(number(offset + 4, 8))
        position = 20 + size
    elif number(4) != len(pcm) - 8:
        return False
    fmt, data = None, None
    while position < len(pcm):
        if position + 8 > len(pcm):
            return False
        kind, size = pcm[position:position + 4], number(position + 4)
        if size == 0xffffffff:
            if magic != b'RF64':
                return False
            if kind == b'data' and data is None:
                size = data_size
            elif extended.get(kind):
                size = extended[kind].pop(0)
            else:
                return False
        start = position + 8
        position = start + size + size % 2
        if position > len(pcm):
            return False
        if kind == b'fmt ':
            if fmt is not None or size < 16 or (size != 16 and (size < 18 or number(start + 16, 2) != size - 18)):
                return False
            fmt = struct.unpack(order + 'HHIIHH', pcm[start:start + 16])
            tag, channels, rate, byte_rate, align, bits = fmt
            if not all((tag, channels, rate, byte_rate, align)):
                return False
            if tag == 1 and (not bits or bits % 8 or align != channels * (bits // 8) or byte_rate != rate * align):
                return False
        elif kind == b'data':
            if fmt is None or data is not None or (data_size is not None and size != data_size):
                return False
            data = size
    if fmt is None or data is None or any(extended.values()):
        return False
    if fmt[0] == 1 and (data % fmt[4] or (sample_count not in (None, 0, data // fmt[4]))):
        return False
    return True


class Simulator:
    """One bounded capture lineage, with multiple sequential streams.

    Keep this object across reconnects: its private sent-range ledger survives
    start(). A fresh object means a separate synthetic lineage, NOT recovery.
    All guards and query evidence are injected test facts, never real proofs.
    """

    MAX_SCRIPT = 128
    MAX_TEXT = 20000
    MAX_FRAME = 32000
    MAX_BYTES = 128000
    MAX_FRAMES = 128
    MAX_STREAMS = 16

    def __init__(self):
        self.state = 'idle'
        self._streams = set()
        self._sent = []
        self._session = None
        self._epoch = None
        self._script = []
        self._position = 0
        self._pending_bytes = 0
        self._revisions = {}
        self._partial_revisions = {}
        self._seen = {}
        self._quarantine = set()
        self._terminal = False
        self._commit_failures = 0

    @property
    def pending_bytes(self):
        return self._pending_bytes

    @property
    def quarantine(self):
        return tuple(self._quarantine)

    def start(self, config):
        if self.state not in ('idle', 'unknown', 'succeeded', 'failed'):
            raise ProtocolError('stream already active')
        allowed = {'durable_audio', 'budget_permit', 'consent', 'dispatch_intent',
                   'class_session_id', 'capture_epoch_id', 'stream_id', 'script',
                   'commit_failures', 'query_result'}
        if type(config) is not dict or set(config) - allowed:
            raise ProtocolError('invalid config')
        if any(config.get(k) is not True for k in
               ('durable_audio', 'budget_permit', 'consent', 'dispatch_intent')):
            raise ProtocolError('all synthetic start guards required')
        session = uid(config['class_session_id'])
        epoch = uid(config['capture_epoch_id'])
        stream = uid(config['stream_id'])
        if self._session is not None and (session, epoch) != (self._session, self._epoch):
            raise ProtocolError('lineage cannot change during reconnect')
        if stream in self._streams or len(self._streams) >= self.MAX_STREAMS:
            raise ProtocolError('new bounded stream identity required')
        script = config.get('script', [])
        self._validate_script(script)
        result = config.get('query_result')
        if result is not None:
            if type(result) is not dict or set(result) != {'proven', 'script'} or result['proven'] is not True:
                raise ProtocolError('query requires synthetic positive proof')
            self._validate_script(result['script'])
            if not result['script'] or result['script'][-1] != {'kind': 'terminal'}:
                raise ProtocolError('query proof must include terminal')
        failures = integer(config.get('commit_failures', 0), maximum=128)
        self._session, self._epoch, self._stream = session, epoch, stream
        self._streams.add(stream)
        self._script = deepcopy(script)
        self._query_result = deepcopy(result)
        self._position = self._pending_bytes = self._frames = self._seq = 0
        self._last_end = None
        self._ranges = []
        self._terminal = False
        self._commit_failures = failures
        self._revisions, self._seen, self._quarantine = {}, {}, set()
        self._partial_revisions = {}
        self.state = 'streaming'
        return self._receipt()

    def _validate_script(self, script):
        if type(script) is not list or len(script) > self.MAX_SCRIPT:
            raise ProtocolError('script bound')
        for index, action in enumerate(script):
            if type(action) is not dict:
                raise ProtocolError('invalid action')
            kind = action.get('kind')
            if kind in ('terminal', 'lost', 'failure'):
                if set(action) != {'kind'}:
                    raise ProtocolError('invalid control action')
                if index != len(script) - 1:
                    raise ProtocolError('control outcome must end script')
                continue
            keys = {'kind', 'segment_id', 'revision', 'sequence', 'start_sample', 'end_sample', 'text'}
            if kind == 'final':
                keys.add('timestamp_source')
            if kind not in ('partial', 'final') or set(action) != keys:
                raise ProtocolError('invalid transcript action')
            uid(action['segment_id'])
            integer(action['revision'], 1)
            integer(action['sequence'])
            start = integer(action['start_sample'])
            if integer(action['end_sample']) <= start:
                raise ProtocolError('half-open segment range required')
            if type(action['text']) is not str or len(action['text']) > self.MAX_TEXT:
                raise ProtocolError('text bound')
            if kind == 'final' and action['timestamp_source'] not in ('provider_segment', 'capture_mapping'):
                raise ProtocolError('invalid timestamp source')

    def send(self, frame):
        if self.state != 'streaming':
            raise ProtocolError('not accepting frames')
        keys = {'pcm', 'sample_rate', 'channels', 'encoding', 'start_sample', 'end_sample',
                'sample_count', 'byte_length', 'sha256'}
        if type(frame) is not dict or set(frame) != keys:
            raise ProtocolError('invalid frame descriptor')
        pcm = frame['pcm']
        if type(pcm) is not bytes or not pcm or len(pcm) > self.MAX_FRAME or len(pcm) % 2:
            raise ProtocolError('PCM length bound')
        if _wave_container(pcm):
            raise ProtocolError('WAV container is not raw PCM')
        if (integer(frame['sample_rate']), integer(frame['channels']), frame['encoding']) != (16000, 1, 'signed16le'):
            raise ProtocolError('PCM format required')
        start, end = integer(frame['start_sample']), integer(frame['end_sample'])
        count = integer(frame['sample_count'], 1)
        if end - start != count or integer(frame['byte_length']) != count * 2 or len(pcm) != count * 2:
            raise ProtocolError('range/length mismatch')
        if frame['sha256'] != sha256(pcm).hexdigest():
            raise ProtocolError('hash mismatch')
        if self._last_end is not None and start != self._last_end:
            raise ProtocolError('frames must be contiguous')
        if any(start < b and a < end for a, b in self._sent):
            raise ProtocolError('already sent range: never automatically resend')
        if self._frames >= self.MAX_FRAMES or len(self._sent) >= self.MAX_FRAMES or self._pending_bytes + len(pcm) > self.MAX_BYTES:
            raise ProtocolError('backpressure: retain caller audio')
        self._sent.append((start, end))
        self._ranges.append((start, end))
        self._last_end = end
        self._frames += 1
        self._pending_bytes += len(pcm)
        return self._receipt()

    def finish(self):
        if self.state == 'streaming':
            self.state = 'draining'
        elif self.state != 'draining':
            raise ProtocolError('cannot finish this state')
        return self._receipt()

    def _receipt(self):
        return {'stream_id': self._stream, 'state': self.state}

    def _payload(self, action):
        final = action['kind'] == 'final'
        data = dict(class_session_id=self._session, stream_id=self._stream,
                    segment_id=action['segment_id'], sequence=action['sequence'],
                    audio_offset_ms=action['start_sample'] // 16,
                    status=action['kind'], text=action['text'], provider_request_id=None,
                    revision=action['revision'])
        event = dict(protocol_version=1, type='transcript.' + action['kind'],
                     sent_at='2026-11-06T00:00:00Z', data=data)
        if final:
            key = (self._stream, action['segment_id'], action['revision'])
            data.update(end_ms=action['end_sample'] // 16,
                        timestamp_source=action['timestamp_source'],
                        segment_revision_id=identity('revision', *key),
                        transcript_version_id=identity('transcript', self._stream))
            event.update(event_id=identity('event', *key), cursor=identity('cursor', *key))
        else:
            event['connection_seq'] = self._seq
        return event

    def events(self):
        """One bounded batch; commit failures leave the action queued for retry."""
        if self.state not in ('streaming', 'draining'):
            return []
        # Preflight the entire remaining batch before any commit or sequence
        # advance. A later invalid action must not strand earlier committed
        # events in a local output list that the caller never receives.
        self._validate_script(self._script)
        for action in self._script[self._position:]:
            if action['kind'] in ('partial', 'final'):
                covered = action['start_sample']
                for start, end in self._ranges:
                    if start > covered:
                        break
                    if start <= covered < end:
                        covered = end
                    if covered >= action['end_sample']:
                        break
                if covered < action['end_sample']:
                    raise ProtocolError('transcript outside sent audio')
        output = []
        while self._position < len(self._script):
            action = self._script[self._position]
            kind = action['kind']
            if kind == 'terminal':
                if self.state != 'draining':
                    break
                self._terminal = True
                # No more scripted outcomes can arrive after terminal. Missing
                # final evidence is queryable unknown, never success or an
                # indefinitely active drain. Earlier retryable commits stop
                # before reaching this action and retain their draining state.
                if not self._seen or any(
                    self._revisions.get(segment, 0) < revision
                    for segment, revision in self._partial_revisions.items()
                ):
                    self.state = 'unknown'
                else:
                    self.state = 'succeeded'
                self._pending_bytes = 0
            elif kind in ('lost', 'failure'):
                self.state = 'unknown' if kind == 'lost' else 'failed'
                self._pending_bytes = 0
            else:
                event = self._payload(action)
                if kind == 'final':
                    key = (action['segment_id'], action['revision'])
                    if key in self._seen:
                        if self._seen[key] != action:
                            self._quarantine.add(key)
                    elif action['revision'] >= self._revisions.get(action['segment_id'], 0):
                        if self._commit_failures:
                            self._commit_failures -= 1
                            break
                        self._seen[key] = deepcopy(action)
                        self._revisions[action['segment_id']] = action['revision']
                        output.append(event)
                elif (action['revision'] > self._revisions.get(action['segment_id'], 0)
                      and action['revision'] >= self._partial_revisions.get(action['segment_id'], 0)):
                    output.append(event)
                    self._partial_revisions[action['segment_id']] = action['revision']
                    self._seq += 1
            self._position += 1
            if self.state in ('unknown', 'failed', 'succeeded'):
                break
        return output

    def query(self, receipt):
        if type(receipt) is not dict or receipt.get('stream_id') != self._stream:
            raise ProtocolError('receipt does not identify current stream')
        if self._query_result is None:
            return {'status': 'unsupported'}
        if self.state != 'unknown':
            return {'status': self.state}
        # Proof does not itself publish or imply commit. Reconciliation reuses
        # stable identities and the normal commit gate, with a bounded script.
        self._script = deepcopy(self._query_result['script'])
        self._position = 0
        self.state = 'draining'
        try:
            emitted = self.events()
        finally:
            if self.state != 'succeeded':
                self.state = 'unknown'
        return {'status': self.state, 'events': emitted}
