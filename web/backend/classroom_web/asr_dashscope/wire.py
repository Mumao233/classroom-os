"""Wire codec for the DashScope Qwen-Audio-ASR-Streaming WebSocket API."""

from dataclasses import dataclass, field
import json
import math

from ..asr_sim.simulator import ProtocolError, integer, uid


MODEL = "qwen-audio-3.1-asr-flash-streaming"
REGION = "cn-beijing"
MAX_MESSAGE_CHARS = 1_048_576
MAX_TEXT_CHARS = 20_000


class UsageError(ProtocolError):
    """A malformed or conflicting cumulative token observation."""


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Sentence:
    sentence_id: int
    begin_ms: int
    end_ms: int | None
    text: str = field(repr=False)
    final: bool


@dataclass(frozen=True)
class WireEvent:
    kind: str
    request_id: str | None = field(default=None, repr=False)
    sentence: Sentence | None = None
    usage: TokenUsage | None = None


def run_task(provider_task_id: str) -> dict:
    return {
        "header": {"action": "run-task", "task_id": uid(provider_task_id), "streaming": "duplex"},
        "payload": {
            "task_group": "audio",
            "task": "asr",
            "function": "recognition",
            "model": MODEL,
            "parameters": {"format": "pcm", "sample_rate": 16000},
            "input": {},
        },
    }


def finish_task(provider_task_id: str) -> dict:
    return {
        "header": {"action": "finish-task", "task_id": uid(provider_task_id), "streaming": "duplex"},
        "payload": {"input": {}},
    }


def _object(value: object) -> dict:
    if not isinstance(value, dict):
        raise ProtocolError("expected a DashScope object")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate DashScope JSON key")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise ProtocolError("invalid DashScope JSON number")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        _invalid_constant(value)
    return result


def _text(value: object, max_chars: int) -> str:
    if not isinstance(value, str) or len(value) > max_chars:
        raise ProtocolError("invalid DashScope text field")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ProtocolError("invalid DashScope text field") from None
    return value


def _sentence(value: object) -> tuple[Sentence, bool]:
    body = _object(value)
    sentence_id = integer(body.get("sentence_id"))
    begin_ms = integer(body.get("begin_time"))
    final = body.get("sentence_end")
    heartbeat = body.get("heartbeat", False)
    if type(final) is not bool or type(heartbeat) is not bool or "end_time" not in body:
        raise ProtocolError("invalid DashScope sentence flags or timing")
    if "sentence_begin" in body and type(body["sentence_begin"]) is not bool:
        raise ProtocolError("invalid DashScope sentence start flag")
    end_ms = body["end_time"]
    if end_ms is not None:
        end_ms = integer(end_ms, minimum=begin_ms)
    if heartbeat != (sentence_id == 0) or (final and (heartbeat or end_ms in (None, begin_ms))):
        raise ProtocolError("invalid DashScope sentence identity or range")
    return Sentence(sentence_id, begin_ms, end_ms, _text(body.get("text"), MAX_TEXT_CHARS), final), heartbeat


def _request_id(output: dict) -> str | None:
    if "request_id" not in output:
        return None
    request_id = _text(output["request_id"], 200)
    if not request_id or not request_id.isprintable() or request_id.isspace():
        raise ProtocolError("invalid DashScope request identity")
    return request_id


def _usage(*containers: dict) -> TokenUsage | None:
    # Each advertised token observation must be complete. A duration-only
    # heartbeat has no 3.1 token evidence and cannot reset the high water mark.
    observed = {}
    try:
        for container in containers:
            if "usage" not in container:
                continue
            body = _object(container["usage"])
            counts = {key: integer(body[key]) for key in
                      ("duration", "input_tokens", "output_tokens", "total_tokens") if key in body}
            token_fields = counts.keys() & {"input_tokens", "output_tokens", "total_tokens"}
            if token_fields:
                if not {"input_tokens", "output_tokens"} <= token_fields:
                    raise UsageError("incomplete DashScope token usage")
                total = counts["input_tokens"] + counts["output_tokens"]
                if "total_tokens" in counts and counts["total_tokens"] != total:
                    raise UsageError("inconsistent DashScope token total")
            for key, count in counts.items():
                if key in observed and observed[key] != count:
                    raise UsageError("conflicting DashScope usage copies")
                observed[key] = count
    except UsageError:
        raise
    except ProtocolError:
        raise UsageError("invalid DashScope usage") from None
    if "input_tokens" in observed:
        return TokenUsage(observed["input_tokens"], observed["output_tokens"])
    return None


def parse_event(raw: str, provider_task_id: str) -> WireEvent:
    """Validate the documented envelope and compare known fields in mirrors."""
    if not isinstance(raw, str) or len(raw) > MAX_MESSAGE_CHARS:
        raise ProtocolError("invalid DashScope JSON message size or type")
    try:
        message = _object(json.loads(raw, object_pairs_hook=_unique_object,
                                     parse_constant=_invalid_constant, parse_float=_finite_float))
    except ProtocolError:
        raise
    except (ValueError, RecursionError):
        raise ProtocolError("invalid DashScope JSON") from None

    header = _object(message.get("header"))
    if uid(header.get("task_id")) != uid(provider_task_id):
        raise ProtocolError("DashScope task identity mismatch")
    if "attributes" in header:
        _object(header["attributes"])
    kind = header.get("event")
    if not isinstance(kind, str) or kind not in ("task-started", "result-generated", "task-finished", "task-failed"):
        raise ProtocolError("unknown DashScope event type")
    payload = _object(message.get("payload"))
    if kind == "task-started":
        return WireEvent(kind)
    if kind == "task-failed":
        if not _text(header.get("error_code"), 200):
            raise ProtocolError("invalid DashScope failure code")
        _text(header.get("error_message"), MAX_TEXT_CHARS)
        return WireEvent(kind)
    if kind == "task-finished":
        return WireEvent(kind, usage=_usage(payload))

    output = _object(payload.get("output"))
    sentence, heartbeat = _sentence(output.get("sentence"))
    request_id = _request_id(output)
    mirror = _object(output["output"]) if "output" in output else {}
    if "sentence" in mirror and _sentence(mirror["sentence"]) != (sentence, heartbeat):
        raise ProtocolError("conflicting DashScope sentence copies")
    mirror_request_id = _request_id(mirror)
    if request_id is not None and mirror_request_id is not None and request_id != mirror_request_id:
        raise ProtocolError("conflicting DashScope request identities")
    return WireEvent(kind, request_id or mirror_request_id, None if heartbeat else sentence,
                     _usage(payload, output, mirror))
