"""Capture values shared by HTTP ingest, durable receipts and the future ASR consumer."""
from dataclasses import dataclass
from typing import Protocol

from .auth import AuthRejected


PCM_SAMPLE_RATE = 16_000
MAX_CHUNK_BYTES = 32_000
MAX_SEQUENCE = 50_000


class CaptureLeaseConflict(AuthRejected):
    """A current owner's CAS target, exposed only after classroom authorization."""
    def __init__(self, lease_id=None):
        super().__init__('LEASE_CONFLICT', 409)
        self.conflict_id = str(lease_id) if lease_id is not None else None


def validate_chunk(body, *, max_samples, max_chunks):
    for field in ('seq', 'start_sample', 'end_sample', 'byte_length', 'fence_token'):
        if type(body.get(field)) is not int:
            raise AuthRejected('VALIDATION_FAILED', 422)
    if (not 0 <= body['seq'] < max_chunks
            or not 0 <= body['start_sample'] < body['end_sample'] <= max_samples
            or body['byte_length'] != 2 * (body['end_sample'] - body['start_sample'])
            or not 2 <= body['byte_length'] <= MAX_CHUNK_BYTES
            or body['fence_token'] < 1):
        raise AuthRejected('VALIDATION_FAILED', 422)


def sequence_ranges(sequences):
    """Compress sorted unique sequence facts without inventing continuity."""
    ranges = []
    for sequence in sorted(set(sequences)):
        if ranges and ranges[-1]['last_seq'] + 1 == sequence:
            ranges[-1]['last_seq'] = sequence
        else:
            ranges.append(dict(first_seq=sequence, last_seq=sequence))
    return ranges


def missing_ranges(durable, last_sequence):
    if last_sequence is None:
        return []
    result = []
    first = 0
    for value in sequence_ranges(durable):
        if first < value['first_seq']:
            result.append(dict(first_seq=first, last_seq=value['first_seq'] - 1))
        first = value['last_seq'] + 1
    if first <= last_sequence:
        result.append(dict(first_seq=first, last_seq=last_sequence))
    return result


@dataclass(frozen=True)
class DurableCaptureChunk:
    """ASR input fact. Its existence grants no provider submission or payment authority."""
    chunk_id: str
    class_session_id: str
    capture_epoch_id: str
    seq: int
    start_sample: int
    end_sample: int
    sha256: str
    asset_id: str
    ingest_mode: str
    receipt_id: str


class CaptureSource(Protocol):
    def durable_source(self, context, chunk_id: str) -> DurableCaptureChunk: ...
