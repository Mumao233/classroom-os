"""Sample ranges and adapters' PCM input for durable Web capture."""
from dataclasses import dataclass, field
import hashlib

from .capture import DurableCaptureChunk, MAX_CHUNK_BYTES, PCM_SAMPLE_RATE

SAMPLES_PER_MS = PCM_SAMPLE_RATE // 1_000
FRAME_SAMPLES = PCM_SAMPLE_RATE // 10


@dataclass(frozen=True, order=True)
class Samples:
    start: int
    end: int

    def __post_init__(self):
        if type(self.start) is not int or type(self.end) is not int or not 0 <= self.start < self.end:
            raise ValueError('invalid half-open sample range')

    def overlaps(self, other):
        return self.start < other.end and other.start < self.end

    def contains(self, other):
        return self.start <= other.start and other.end <= self.end

    @property
    def count(self):
        return self.end - self.start

    @property
    def duration_ms(self):
        return (self.count + SAMPLES_PER_MS - 1) // SAMPLES_PER_MS

    def classroom_ms(self, offset):
        """The adapter already uses the capture epoch sample coordinates."""
        return (offset + self.start // SAMPLES_PER_MS,
                offset + (self.end + SAMPLES_PER_MS - 1) // SAMPLES_PER_MS)


@dataclass(frozen=True)
class CaptureFrame:
    source: DurableCaptureChunk
    samples: Samples
    pcm: bytes = field(repr=False)

    def provider_value(self):
        return {
            'pcm': self.pcm,
            'start_sample': self.samples.start,
            'end_sample': self.samples.end,
            'sample_rate': PCM_SAMPLE_RATE,
            'channels': 1,
            'encoding': 'signed16le',
            'sample_count': self.samples.count,
            'byte_length': len(self.pcm),
            'sha256': hashlib.sha256(self.pcm).hexdigest(),
        }


def source_frames(source: DurableCaptureChunk, pcm: bytes, selected: Samples):
    """Verify the whole private object before returning any sendable bytes."""
    complete = Samples(source.start_sample, source.end_sample)
    if not complete.contains(selected):
        raise ValueError('selected samples exceed the durable source')
    if (type(pcm) is not bytes or len(pcm) != 2 * complete.count or len(pcm) > MAX_CHUNK_BYTES
            or hashlib.sha256(pcm).hexdigest() != source.sha256):
        raise ValueError('PCM does not match its durable source')
    frames = []
    for start in range(selected.start, selected.end, FRAME_SAMPLES):
        end = min(start + FRAME_SAMPLES, selected.end)
        frame = pcm[2 * (start - complete.start):2 * (end - complete.start)]
        frames.append(CaptureFrame(source, Samples(start, end), frame))
    return tuple(frames)
