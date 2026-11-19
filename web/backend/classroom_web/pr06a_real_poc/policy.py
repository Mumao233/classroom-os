"""Frozen PR-06a authorization and sample inventory."""
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from unicodedata import normalize
import wave

from ..asr_dashscope.transport import AuthenticationRejected, Disconnected
from ..asr_dashscope.wire import MODEL, REGION

AUTHORIZATION = "PR06A-REAL-POC-AUTH-20261118"
AUTH_SHA256 = "233859c7a90ea2ddb0b3b7069edb39b20a82a88cc889c363297d31e96809b777"
SECOND_AUTHORIZATION = "PR06A-REAL-POC-AUTH-20261119-B2"
SECOND_AUTH_SHA256 = "9a56d2ea65799d821b19120a080b00d51689bc9644dce013f04213e4169d8047"
THIRD_AUTHORIZATION = "PR06A-REAL-POC-AUTH-20261119-B3"
THIRD_AUTH_SHA256 = "613acf9ba5fca8033e41bcebb34a7b6b0ad307e116733326031ff46e7cab80c2"
FOURTH_AUTHORIZATION = "PR06A-REAL-POC-AUTH-20261120-B4"
FOURTH_AUTH_SHA256 = "405f31b2836c711436aebb7f8bfe62db609f3960923861f4426361bbda13aa9a"
AUTHORIZATION_DIGESTS = {
    AUTHORIZATION: AUTH_SHA256,
    SECOND_AUTHORIZATION: SECOND_AUTH_SHA256,
    THIRD_AUTHORIZATION: THIRD_AUTH_SHA256,
    FOURTH_AUTHORIZATION: FOURTH_AUTH_SHA256,
}
BASELINE = "6ecd364d20bbd06a25fa4654570e454a04807e07"
OPERATOR = "林珈诚"
WORKSPACE = "ws-bm2ftvz01aej1eof"
KEY_ID = "8245225"
ENDPOINT = f"wss://{WORKSPACE}.{REGION}.maas.aliyuncs.com/api-ws/v1/inference"
LIB_VERSION = "17.4"
MAX_CONNECTION_SECONDS = 10
MAX_BATCH_SECONDS = 30
FRAME_SAMPLES = 1600
CLOSE_WAIT_SECONDS = 3
BUDGET = 1_000_000
MAX_WAV_BYTES = 320_256
_DIGITS = "零一二三四五六七八九"
_NUMERAL_CHARS = f"0-9{_DIGITS}〇两十百千万亿"
_SINGLE_DIGIT = re.compile(rf"(?<![{_NUMERAL_CHARS}])([{_DIGITS}])(?=[点楼])")
_NUMERIC_PUNCTUATION = re.compile(
    rf"(?<=[{_NUMERAL_CHARS}])[\W_]+(?=[{_NUMERAL_CHARS}])|[-+](?=\.?[{_NUMERAL_CHARS}])|\.(?=[{_NUMERAL_CHARS}])"
)
_NUMERIC_GLYPHS = str.maketrans("−–—⁄∕", "---//")


@dataclass(frozen=True)
class CloseAudit:
    status: str
    elapsed_ms: int
    received_code: int | None
    received_reason: str | None
    sent_code: int | None
    sent_reason: str | None
    initiator: str | None


def compare_transcript(actual: str, reference: str, *, allow_prefix: bool = False) -> str:
    def comparable(text):
        text = "".join(normalize("NFKC", text).casefold().split()).translate(_NUMERIC_GLYPHS)
        text = _SINGLE_DIGIT.sub(lambda match: str(_DIGITS.index(match[1])), text)
        numeric_punctuation = {i for match in _NUMERIC_PUNCTUATION.finditer(text) for i in range(*match.span())}
        return "".join(ch for i, ch in enumerate(text) if ch.isalnum() or i in numeric_punctuation)

    actual, reference = comparable(actual), comparable(reference)
    if not actual or not reference:
        return "unavailable"
    if actual == reference:
        return "match"
    return "prefix_match" if allow_prefix and reference.startswith(actual) else "differs"


class PocStopped(Exception):
    """Fixed error code safe for ordinary operational output."""


class RequestConflict(PocStopped):
    """The database definitely rejected a conflicting request identity."""


class EndOfStream(ConnectionError):
    """The peer closed the WebSocket normally."""


class ConnectFailure(Disconnected):
    """A fixed, credential-free diagnostic code from the managed WSS dial."""


class ConnectAuthenticationFailure(AuthenticationRejected, ConnectFailure):
    """The opening HTTP request was rejected with 401 or 403."""


def sentence_proof(sentence):
    return (sentence.sentence_id, sentence.begin_ms, sentence.end_ms,
            sha256(sentence.text.encode("utf-8")).hexdigest())


class UnsafeInbound(PocStopped):
    def __init__(self, reason, checksum, metering=None, *, observation=None, control=0, event=None, final_sentence=None):
        super().__init__(reason)
        self.checksum = checksum
        self.metering = metering
        self.observation = observation or checksum
        self.control = control
        self.event = event
        self.final_sentence = final_sentence


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


def authorization_digest(authorization_id: str) -> str:
    try:
        return AUTHORIZATION_DIGESTS[authorization_id]
    except KeyError:
        raise PocStopped("unregistered_authorization") from None


def verify_authorization(authorization: Path, authorization_id: str) -> str:
    expected = authorization_digest(authorization_id)
    if digest(authorization.read_bytes()) != expected:
        raise PocStopped("authorization_digest_mismatch")
    return expected


def load_samples(directory: Path, authorization: Path, authorization_id: str = AUTHORIZATION) -> tuple[Sample, ...]:
    verify_authorization(authorization, authorization_id)
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
