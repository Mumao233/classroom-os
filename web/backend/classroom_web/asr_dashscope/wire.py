"""Wire codec for the DashScope Qwen-Audio-ASR-Streaming WebSocket API."""

from dataclasses import dataclass, field
from hashlib import sha256
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
    usage_issue: bool = False
    sentence_issue: bool = False
    heartbeat: tuple[int, int | None] | None = None
    heartbeat_duration: int | None = None


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
    if not isinstance(value, str) or len(value) > max_chars or "\x00" in value:
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


def _request_id(*outputs: dict) -> str | None:
    request_id = None
    for output in outputs:
        if "request_id" not in output:
            continue
        candidate = _text(output["request_id"], 200)
        if not candidate or not candidate.isprintable() or candidate.isspace():
            raise ProtocolError("invalid DashScope request identity")
        if request_id is not None and request_id != candidate:
            raise ProtocolError("conflicting DashScope request identities")
        request_id = candidate
    return request_id


def _usage(*containers: dict) -> tuple[TokenUsage | None, int | None]:
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
    tokens = None
    if "input_tokens" in observed:
        tokens = TokenUsage(observed["input_tokens"], observed["output_tokens"])
    return tokens, observed.get("duration")


@dataclass(frozen=True)
class RiskTokens:
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class MeteringEvidence:
    task_id: str
    request_id: str | None = field(repr=False)
    usage: RiskTokens | None
    total: int | None
    attribution_conflict: bool = False
    request_fingerprint: str | None = None
    metering_conflict: bool = False


class _ObjectPairs(list):
    pass


def _values(obj, key):
    return [value for name, value in obj if name == key] if isinstance(obj, _ObjectPairs) else []


def _unknown_number(_value):
    return None


def _bounded_meter_int(value):
    return int(value) if len(value) <= 17 else (2**53 if not value.startswith("-") else None)


def _meter_totals(containers):
    high = {}
    conflict = False
    for body in containers:
        current = {}
        for meter in ("input_tokens", "output_tokens", "total_tokens"):
            values = _values(body, meter)
            if not values:
                continue
            counts = {min(value, 2**53 - 1) for value in values if type(value) is int and value >= 0}
            conflict |= (len(counts) != 1 or any(type(v) is not int or not 0 <= v <= 2**53 - 1 for v in values))
            if not counts:
                continue
            count = min(counts)
            current[meter] = count
            if meter in high and high[meter] != count:
                conflict = True
            high[meter] = max(high.get(meter, 0), count)
        if "total_tokens" in current:
            floor = current.get("input_tokens", 0) + current.get("output_tokens", 0)
            conflict |= current["total_tokens"] < floor
            if "input_tokens" in current and "output_tokens" in current:
                conflict |= current["total_tokens"] != floor
    floor = high.get("input_tokens", 0) + high.get("output_tokens", 0)
    both = "input_tokens" in high and "output_tokens" in high
    if "total_tokens" in high:
        conflict |= high["total_tokens"] < floor or (both and high["total_tokens"] != floor)
    elif both:
        high["total_tokens"] = floor
    split = None
    if "input_tokens" in high or "output_tokens" in high:
        split = RiskTokens(high.get("input_tokens"), high.get("output_tokens"))
    return split, high.get("total_tokens"), conflict


def metering_evidence(raw: str) -> MeteringEvidence | None:
    """Extract only task, request, and counters from known metering paths."""
    if len(raw) > MAX_MESSAGE_CHARS:
        return None
    try:
        obj = json.loads(raw, object_pairs_hook=_ObjectPairs, parse_constant=_unknown_number,
                         parse_float=_unknown_number, parse_int=_bounded_meter_int)
        tasks = {uid(task) for header in _values(obj, "header") for task in _values(header, "task_id")}
        if len(tasks) != 1:
            return None
        task = tasks.pop()
        payloads = _values(obj, "payload")
        outputs = [out for payload in payloads for out in _values(payload, "output")]
        mirrors = [mirror for out in outputs for mirror in _values(out, "output")]
        try:
            ids = {_request_id({"request_id": item})
                   for container in (*outputs, *mirrors) for item in _values(container, "request_id")}
        except ProtocolError:
            return MeteringEvidence(task, None, None, None, attribution_conflict=True)
        if len(ids) > 1:
            return MeteringEvidence(task, None, None, None, attribution_conflict=True)
        usage, total, conflict = _meter_totals(
            body for container in (*payloads, *outputs, *mirrors) for body in _values(container, "usage"))
        return MeteringEvidence(task, next(iter(ids), None), usage, total, metering_conflict=conflict)
    except (ValueError, RecursionError):
        return None


def observation_fingerprint(raw: str) -> str:
    try:
        obj = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant, parse_float=_finite_float)
        raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (ValueError, RecursionError):
        pass
    return sha256(raw.encode("utf-8")).hexdigest()


def parse_event(raw: str, provider_task_id: str) -> WireEvent:
    """Validate identity before separating transcript and metering evidence."""
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

    output_value = payload.get("output")
    output = output_value if isinstance(output_value, dict) else {}
    mirror_value = output.get("output", {})
    mirror = mirror_value if isinstance(mirror_value, dict) else {}
    request_id = _request_id(output, mirror)

    usage_issue = False
    try:
        usage, duration = _usage(payload, output, mirror)
        if kind == "task-finished":
            if ("output" in payload and not isinstance(output_value, dict)
                    or not isinstance(mirror_value, dict)):
                raise UsageError("invalid DashScope terminal usage mirror")
            if usage is not None and "input_tokens" not in payload.get("usage", {}):
                raise UsageError("missing canonical DashScope terminal usage")
    except UsageError:
        usage, duration, usage_issue = None, None, True

    sentence, heartbeat, sentence_issue = None, None, False
    if kind == "result-generated":
        try:
            candidate, is_heartbeat = _sentence(output.get("sentence"))
            _object(mirror_value)
            if "sentence" in mirror and _sentence(mirror["sentence"]) != (candidate, is_heartbeat):
                raise ProtocolError("conflicting DashScope sentence copies")
        except ProtocolError:
            sentence_issue = True
        else:
            if is_heartbeat:
                heartbeat = candidate.begin_ms, candidate.end_ms
            else:
                sentence = candidate

    # Duration can distinguish otherwise identical heartbeat observations. It
    # supplies neither 3.1 token evidence nor a chargeable audio measurement.
    return WireEvent(kind, request_id=request_id, sentence=sentence, usage=usage,
                     usage_issue=usage_issue, sentence_issue=sentence_issue, heartbeat=heartbeat,
                     heartbeat_duration=duration if heartbeat is not None else None)
