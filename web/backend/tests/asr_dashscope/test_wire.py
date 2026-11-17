"""Sanitized fixtures for the documented DashScope JSON message shapes."""

import json

import pytest

from classroom_web.asr_dashscope.wire import (
    MAX_MESSAGE_CHARS,
    MAX_TEXT_CHARS,
    MODEL,
    REGION,
    Sentence,
    TokenUsage,
    WireEvent,
    finish_task,
    parse_event,
    run_task,
)
from classroom_web.asr_sim import ProtocolError


TASK = "3d205ce7-1d49-4850-a23f-13e0f29bedb0"
OTHER_TASK = "fb1b224a-c3bb-4504-9518-72a77b1190dc"
REQUEST = "provider-request-example"


def parse(kind, payload=None, **header):
    return parse_event(json.dumps({
        "header": {"task_id": TASK, "event": kind, **header},
        "payload": {} if payload is None else payload,
    }), TASK)


def sentence(**changes):
    return {"sentence_id": 1, "begin_time": 0, "end_time": None, "text": "测试",
            "sentence_end": False, **changes}


def result(body=None, **fields):
    return {"output": {"sentence": sentence() if body is None else body, **fields}}


def test_run_and_finish_commands_use_documented_wire_contract():
    assert REGION == "cn-beijing"
    assert MODEL == "qwen-audio-3.1-asr-flash-streaming"
    assert run_task(TASK) == {
        "header": {"action": "run-task", "task_id": TASK, "streaming": "duplex"},
        "payload": {"task_group": "audio", "task": "asr", "function": "recognition", "model": MODEL,
                    "parameters": {"format": "pcm", "sample_rate": 16000}, "input": {}},
    }
    assert finish_task(TASK) == {
        "header": {"action": "finish-task", "task_id": TASK, "streaming": "duplex"},
        "payload": {"input": {}},
    }


def test_task_started_allows_response_metadata():
    assert parse("task-started", attributes={}, task_uuid="ef13f34ee98d40cd8da8b1d49dd53e514") == WireEvent(
        "task-started"
    )


def test_sentence_begin_partial_and_final_keep_sentence_id_while_begin_time_changes():
    empty = parse("result-generated", result(sentence(text="", sentence_begin=True)))
    partial = parse("result-generated", result(request_id=REQUEST))
    final = parse("result-generated", result(sentence(begin_time=160, end_time=540, sentence_end=True),
                                             request_id=REQUEST))
    assert empty.sentence == Sentence(1, 0, None, "", False)
    assert partial == WireEvent("result-generated", REQUEST, Sentence(1, 0, None, "测试", False))
    assert final == WireEvent("result-generated", REQUEST, Sentence(1, 160, 540, "测试", True))


def test_known_mirrors_agree_even_with_extra_fields_and_usage_is_counted_once():
    primary = sentence(begin_time=160, end_time=540, sentence_end=True,
                       words=[{"text": "测试", "speaker_id": None}], stash={"sentence_id": 2})
    usage = {"duration": 1, "input_tokens": 10, "output_tokens": 3, "total_tokens": 13}
    payload = result(primary, request_id=REQUEST, output={"sentence": {**primary, "channel_id": 0},
                     "request_id": REQUEST}, usage=usage)
    payload["usage"] = {**usage, "additional_meter": 999}
    assert parse("result-generated", payload) == WireEvent(
        "result-generated", REQUEST, Sentence(1, 160, 540, "测试", True), TokenUsage(10, 3)
    )


def test_heartbeat_only_duration_is_not_zero_tokens_or_a_transcript():
    body = sentence(sentence_id=0, text="", heartbeat=True)
    payload = {**result(body, request_id=REQUEST), "usage": {"duration": 2}}
    assert parse("result-generated", payload) == WireEvent("result-generated", REQUEST, heartbeat=(0, None))


def test_moving_heartbeat_is_distinct_from_a_replay_with_same_cumulative_usage():
    body = sentence(sentence_id=0, text="", heartbeat=True)
    payload = {**result(body), "usage": {"input_tokens": 5, "output_tokens": 0}}
    first = parse("result-generated", payload)
    repeat = parse("result-generated", payload)
    later = parse("result-generated", {**payload, **result({**body, "begin_time": 25})})
    assert first == repeat and first != later
    assert first.sentence is later.sentence is None
    assert first.usage == later.usage == TokenUsage(5, 0)
    assert later.heartbeat == (25, None)


@pytest.mark.parametrize("usage,expected", [
    ({"input_tokens": 0, "output_tokens": 0}, TokenUsage(0, 0)),
    ({"input_tokens": 12, "output_tokens": 4, "total_tokens": 16}, TokenUsage(12, 4)),
    ({"duration": 0}, None),
    ({}, None),
])
def test_terminal_usage_requires_actual_token_evidence(usage, expected):
    assert parse("task-finished", {"output": {}, "usage": usage}) == WireEvent("task-finished", usage=expected)
    assert parse("task-finished") == WireEvent("task-finished")


@pytest.mark.parametrize("kind", ["result-generated", "task-finished"])
@pytest.mark.parametrize("usage", [
    None,
    "sensitive-text",
    {"input_tokens": 4},
    {"duration": 10, "total_tokens": 0},
    {"input_tokens": True, "output_tokens": 0},
    {"input_tokens": -1, "output_tokens": 0},
    {"input_tokens": 2**53, "output_tokens": 0},
    {"input_tokens": 4, "output_tokens": 0, "total_tokens": 5},
    {"duration": "2"},
])
def test_malformed_usage_is_flagged_and_redacted(kind, usage):
    if kind == "task-finished":
        event = parse(kind, {"output": {}, "usage": usage})
        assert event == WireEvent("task-finished", usage_issue=True)
    else:
        body = sentence(sentence_end=True, end_time=10)
        event = parse(kind, {**result(body), "usage": usage})
        assert event == WireEvent(kind, sentence=Sentence(1, 0, 10, "测试", True), usage_issue=True)
    assert "sensitive-text" not in repr(event)


def test_malformed_terminal_usage_does_not_bypass_task_identity():
    with pytest.raises(ProtocolError, match="task identity mismatch"):
        parse("task-finished", {"usage": {"input_tokens": 4}}, task_id=OTHER_TASK)


@pytest.mark.parametrize("nested", [
    {"sentence": sentence(text="changed")},
    {"sentence": sentence(begin_time=1)},
    {"sentence": []},
    "invalid mirror container",
])
def test_bad_sentence_mirror_keeps_valid_usage(nested):
    payload = result(request_id=REQUEST, output=nested, usage={"input_tokens": 5, "output_tokens": 2})
    assert parse("result-generated", payload) == WireEvent(
        "result-generated", REQUEST, usage=TokenUsage(5, 2), sentence_issue=True,
    )


@pytest.mark.parametrize("kind", ["result-generated", "task-finished"])
@pytest.mark.parametrize("nested_id", [None, "different-request"])
def test_bad_request_mirror_cannot_emit_disputed_evidence(kind, nested_id):
    payload = {"output": {"request_id": REQUEST, "output": {"request_id": nested_id}},
               "usage": {"input_tokens": 5}}
    with pytest.raises(ProtocolError):
        parse(kind, payload)


def test_conflicting_usage_copies_keep_bound_transcript():
    payload = result(usage={"input_tokens": 5, "output_tokens": 2})
    payload["usage"] = {"input_tokens": 4, "output_tokens": 2}
    assert parse("result-generated", payload) == WireEvent(
        "result-generated", sentence=Sentence(1, 0, None, "测试", False), usage_issue=True,
    )


@pytest.mark.parametrize("mirror", ["outer", "inner"])
def test_terminal_usage_mirrors_cannot_disagree_with_the_canonical_observation(mirror):
    canonical = {"input_tokens": 4, "output_tokens": 2}
    output = {"request_id": REQUEST, "usage": canonical.copy(), "output": {"usage": canonical.copy()}}
    expected = WireEvent("task-finished", REQUEST, usage=TokenUsage(4, 2))
    assert parse("task-finished", {"usage": canonical, "output": output}) == expected
    disputed = output if mirror == "outer" else output["output"]
    disputed["usage"]["output_tokens"] = 3
    assert parse("task-finished", {"usage": canonical, "output": output}) == WireEvent(
        "task-finished", REQUEST, usage_issue=True,
    )


def test_undocumented_nested_only_terminal_usage_remains_pending():
    payload = {"output": {"usage": {"input_tokens": 4, "output_tokens": 2}}}
    assert parse("task-finished", payload) == WireEvent("task-finished", usage_issue=True)


@pytest.mark.parametrize("changes", [
    {"sentence_id": 0},
    {"sentence_id": True},
    {"begin_time": -1},
    {"begin_time": 80, "end_time": 10},
    {"end_time": 0, "sentence_end": True},
    {"sentence_end": True},
    {"sentence_end": "false"},
    {"heartbeat": True},
    {"sentence_begin": 1},
    {"text": None},
    {"text": "a" * (MAX_TEXT_CHARS + 1)},
    {"text": "\ud800"},
])
def test_malformed_sentence_never_constructs_revisions(changes):
    event = parse("result-generated", {**result(sentence(**changes)),
                                       "usage": {"input_tokens": 71, "output_tokens": 8}})
    assert event == WireEvent("result-generated", usage=TokenUsage(71, 8), sentence_issue=True)


@pytest.mark.parametrize("output", [None, [], {}, {"output": {"sentence": sentence()}}])
def test_missing_canonical_sentence_cannot_discard_valid_usage(output):
    usage = {"input_tokens": 80, "output_tokens": 16}
    assert parse("result-generated", {"output": output, "usage": usage}) == WireEvent(
        "result-generated", usage=TokenUsage(80, 16), sentence_issue=True,
    )


def test_vendor_failure_fields_are_consumed_without_echo():
    private_text = "Authorization: Bearer sk-example-private"
    failure = parse("task-failed", error_code="UNSUPPORTED_FORMAT", error_message=private_text)
    assert failure == WireEvent("task-failed")
    assert private_text not in repr(failure)
    with pytest.raises(ProtocolError) as exc:
        parse("task-failed", error_code=private_text)
    assert private_text not in str(exc.value)


@pytest.mark.parametrize("raw", [
    b"{}",
    "{illegal-json",
    "null",
    "[]",
    "{}",
    '{"payload": NaN}',
    '{"payload": 1e999}',
    '{"payload": {}, "payload": {}}',
    "[" * 1_500 + "]" * 1_500,
    " " * (MAX_MESSAGE_CHARS + 1),
], ids=["binary", "bad-json", "null", "array", "missing-envelope", "nan", "infinite",
        "duplicate-key", "deep-nesting", "oversized"])
def test_invalid_json_or_envelope_is_rejected(raw):
    with pytest.raises(ProtocolError):
        parse_event(raw, TASK)


@pytest.mark.parametrize("task_id", ["invalid-uuid", OTHER_TASK, None, 123, TASK.upper()])
def test_task_identity_must_be_the_one_canonical_uuid_for_this_connection(task_id):
    with pytest.raises(ProtocolError):
        parse("task-started", task_id=task_id)


def test_unknown_event_and_missing_required_payload_fail_closed():
    with pytest.raises(ProtocolError, match="unknown DashScope event"):
        parse("future-event")
    with pytest.raises(ProtocolError):
        parse_event(json.dumps({"header": {"event": "task-started", "task_id": TASK}}), TASK)


def test_transcript_and_request_id_are_not_in_wire_event_repr():
    event = parse("result-generated", result(sentence(text="private transcript"), request_id=REQUEST))
    assert "private transcript" not in repr(event)
    assert REQUEST not in repr(event)
