"""Synthetic one-request evidence, scoped to the offline DashScope adapter."""
from dataclasses import dataclass
from threading import Lock

from ..domain.asr import ProtocolError, uid
from ..domain.billing import integer


@dataclass(frozen=True)
class StreamIdentity:
    task_id: str
    attempt_id: str
    stream_id: str
    provider_task_id: str

    def __post_init__(self):
        for identifier in (self.task_id, self.attempt_id, self.stream_id, self.provider_task_id):
            uid(identifier)
        if self.provider_task_id in (self.task_id, self.attempt_id, self.stream_id):
            raise ProtocolError('provider task identity must be allocated separately')


class SyntheticRequestPermit:
    """Trusted test composition supplies one permit object per synthetic receipt.

    Real admission requires a committed PR-11a transaction and verified rates
    and bounds. This in-memory object cannot attest to those facts.
    """

    def __init__(self, *, reservation_id: str, permit_id: str, identity: StreamIdentity,
                 maximum_units: dict[str, int]):
        self._reservation_id = uid(reservation_id)
        self._permit_id = uid(permit_id)
        if type(identity) is not StreamIdentity:
            raise ProtocolError('a permit requires a bound stream identity')
        if type(maximum_units) is not dict or set(maximum_units) != {
            'audio_duration_ms', 'input_tokens', 'output_tokens',
        }:
            raise ProtocolError('separate duration, input and output bounds are required')
        try:
            duration = integer(maximum_units['audio_duration_ms'])
            self._input_tokens_limit = integer(maximum_units['input_tokens'])
            self._output_tokens_limit = integer(maximum_units['output_tokens'])
        except ValueError:
            raise ProtocolError('invalid synthetic meter bound') from None
        if duration == 0:
            raise ProtocolError('a positive duration bound is required')
        self._sample_limit = duration * 16
        self._identity = identity
        self._lock = Lock()
        self._consumed = False

    @property
    def reservation_id(self) -> str:
        return self._reservation_id

    @property
    def permit_id(self) -> str:
        return self._permit_id

    @property
    def input_tokens_limit(self) -> int:
        return self._input_tokens_limit

    @property
    def output_tokens_limit(self) -> int:
        return self._output_tokens_limit

    def consume(self, identity: StreamIdentity) -> None:
        with self._lock:
            if type(identity) is not StreamIdentity or identity != self._identity:
                raise ProtocolError('permit belongs to a different stream')
            if self._consumed:
                raise ProtocolError('permit already consumed')
            self._consumed = True

    def check_audio(self, total_samples: int) -> None:
        with self._lock:
            if not self._consumed:
                raise ProtocolError('permit has not been consumed')
            if type(total_samples) is not int or not 0 < total_samples <= self._sample_limit:
                raise ProtocolError('audio exceeds the reserved duration')
