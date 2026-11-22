"""Independent synthetic file job and the canonical PCM selection."""
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from .asr import SAMPLES_PER_MS, Samples
from .billing import Usage


KIND = 'asr_retranscribe'
SCHEMA = 'pr06c-1'
DESTINATION = {
    'provider': 'filetrans-synthetic',
    'model': 'qwen-audio-3.1-asr-flash-filetrans',
    'region': 'cn-beijing',
    'configuration_revision': SCHEMA,
}
MAX_CHUNKS = 50_000
MAX_SEGMENTS = 256


def selected_groups(chunks):
    """Group contiguous slices while keeping every epoch and gap explicit."""
    groups = []
    for chunk in chunks:
        if (not groups or len(groups[-1]) == 1000
                or groups[-1][-1]['epoch'] != chunk['epoch']
                or groups[-1][-1]['last'] != chunk['first']):
            groups.append([])
        groups[-1].append(chunk)
    return groups


def duration_ms(chunks):
    samples = sum(chunk['last'] - chunk['first'] for chunk in chunks)
    return (samples + SAMPLES_PER_MS - 1) // SAMPLES_PER_MS


@dataclass(frozen=True)
class FilePart:
    chunk: dict
    selected_pcm_sha256: str
    selected_byte_count: int


@dataclass(frozen=True)
class FileReply:
    request_id: str
    text: str | None
    usage: Usage | None


class SyntheticFiletrans:
    """A deterministic batch stand-in. This adapter has no network or credentials."""

    def __init__(self, outcome='success'):
        if outcome not in ('success', 'success_no_usage', 'failure', 'unknown', 'timeout'):
            raise ValueError('unsupported synthetic Filetrans outcome')
        self.outcome = outcome

    def submit(self, attempt_id: UUID, parts: tuple[FilePart, ...]) -> FileReply:
        if not parts:
            raise ValueError('a file job requires durable PCM')
        for part in parts:
            if (part.selected_byte_count != 2 * (part.chunk['last'] - part.chunk['first'])
                    or len(part.selected_pcm_sha256) != 64):
                raise ValueError('invalid selected PCM')
        if self.outcome == 'unknown':
            raise ConnectionError('synthetic acknowledgement lost')
        if self.outcome == 'timeout':
            raise TimeoutError('synthetic file job timed out')
        receipt = 'synthetic-file-' + attempt_id.hex
        usage = None if self.outcome == 'success_no_usage' else Usage(provider_request_id=receipt,
            meter='synthetic-filetrans', revision=1, final=True,
            quantities={'audio_duration_ms': duration_ms([part.chunk for part in parts])})
        if self.outcome == 'failure':
            return FileReply(receipt, None, usage)
        digest = sha256(''.join(part.selected_pcm_sha256 for part in parts).encode('ascii')).hexdigest()
        return FileReply(receipt, '合成重转写 ' + digest[:16], usage)


def candidate_segments(chunks, text, request_id):
    for group in selected_groups(chunks):
        first, last = group[0], group[-1]
        start_ms, end_ms = Samples(first['first'], last['last']).classroom_ms(first['offset'])
        yield dict(start_ms=start_ms, end_ms=end_ms, source_chunk_ids=[UUID(item['id']) for item in group],
                   body=text, provider_request_id=request_id)
