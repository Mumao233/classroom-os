"""Frozen PR-06a authorization and sample inventory."""
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import wave

from ..asr_dashscope.wire import MODEL, REGION

AUTHORIZATION = "PR06A-REAL-POC-AUTH-20261118"
AUTH_SHA256 = "233859c7a90ea2ddb0b3b7069edb39b20a82a88cc889c363297d31e96809b777"
BASELINE = "6ecd364d20bbd06a25fa4654570e454a04807e07"
OPERATOR = "林珈诚"
WORKSPACE = "ws-bm2ftvz01aej1eof"
KEY_ID = "8245225"
ENDPOINT = f"wss://{WORKSPACE}.{REGION}.maas.aliyuncs.com/api-ws/v1/inference"
LIB_VERSION = "17.4"
MAX_CONNECTION_SECONDS = 10
MAX_BATCH_SECONDS = 30
FRAME_SAMPLES = 1600
BUDGET = 1_000_000
MAX_WAV_BYTES = 320_256


class PocStopped(Exception):
    """Fixed error code safe for ordinary operational output."""


class UnsafeInbound(PocStopped):
    def __init__(self, reason, checksum):
        super().__init__(reason)
        self.checksum = checksum


@dataclass(frozen=True)
class SampleSpec:
    slot: int
    name: str
    wav_hash: str
    pcm_hash: str
    samples: int
    send_samples: int
    send_hash: str


@dataclass(frozen=True)
class Sample:
    spec: SampleSpec
    pcm: bytes = field(repr=False)


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def load_samples(directory: Path, authorization: Path) -> tuple[Sample, ...]:
    if digest(authorization.read_bytes()) != AUTH_SHA256:
        raise PocStopped("authorization_digest_mismatch")
    registry = json.loads(Path(__file__).with_name("samples.json").read_bytes())
    result = []
    for entry in registry:
        spec = SampleSpec(**entry)
        with (directory / spec.name).open("rb") as file:
            wav = file.read(MAX_WAV_BYTES + 1)
        if len(wav) > MAX_WAV_BYTES or digest(wav) != spec.wav_hash:
            raise PocStopped("wav_digest_mismatch")
        with wave.open(BytesIO(wav), "rb") as audio:
            if audio.getparams()[:5] != (1, 2, 16000, spec.samples, "NONE"):
                raise PocStopped("wav_format_mismatch")
            pcm = audio.readframes(spec.samples)
        prefix = pcm[:2 * spec.send_samples]
        if (len(pcm) != 2 * spec.samples or len(prefix) != 2 * spec.send_samples
                or digest(pcm) != spec.pcm_hash or digest(prefix) != spec.send_hash):
            raise PocStopped("pcm_digest_mismatch")
        result.append(Sample(spec, prefix))
    return tuple(result)
