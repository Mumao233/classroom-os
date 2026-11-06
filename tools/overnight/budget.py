"""Persistent limits for existing Codex subscription use, not an API budget.

This module neither authenticates nor calls a model. No USD spend is authorized.
Token limits are stop conditions on observed usage, not a promise of an exact
pre-request token cap. The executor must also enforce the returned deadline.
"""
from __future__ import annotations

import math
import time

try:
    from .state import JsonStore, StateError, require_keys, require_text, strict_int
except ImportError:  # Direct script directory on sys.path, e.g. a trusted runner.
    from state import JsonStore, StateError, require_keys, require_text, strict_int


DEFAULT_LIMITS = {
    "extra_api_spend_usd": 0,
    "batch_wall_seconds": 14400, "task_wall_seconds": 7200,
    "batch_max_calls": 20, "task_max_calls": 8,
    "batch_max_turns": 20, "task_max_turns": 8,
    "batch_max_input_tokens": None, "batch_max_output_tokens": None,
    "task_max_input_tokens": None, "task_max_output_tokens": None,
    "max_repairs": 3,
}
BLOCK_REASONS = frozenset({"rate_limit", "quota_exhausted", "authentication", "timeout",
    "usage_unknown", "interrupted", "clock_rollback", "limit_exceeded"})


class LimitExceeded(StateError):
    pass


def _validate_limits(limits):
    require_keys(limits, DEFAULT_LIMITS.keys())
    strict_int(limits["extra_api_spend_usd"], "extra_api_spend_usd", 0, 0)
    strict_int(limits["max_repairs"], "max_repairs", 0, 3)
    for key, value in limits.items():
        if key in {"extra_api_spend_usd", "max_repairs"}:
            continue
        if key.endswith("_tokens") and value is None:
            continue
        strict_int(value, key, 1)


class SubscriptionLimits(JsonStore):
    def __init__(self, path, *, clock=time.time):
        super().__init__(path)
        self.clock = clock

    def _now(self):
        value = self.clock()
        if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
            raise StateError("invalid wall clock")
        return int(value * 1000)

    def initialize(self, limits=None):
        supplied = {} if limits is None else limits
        require_keys(supplied, set(), DEFAULT_LIMITS.keys())
        effective = dict(DEFAULT_LIMITS, **supplied)
        _validate_limits(effective)
        now = self._now()
        return self._create({"version": 1, "revision": 0, "events": [], "limits": effective,
            "started_at_ms": now, "last_seen_ms": now, "status": "READY", "block_reason": None,
            "calls": {}, "task_started_at_ms": {}, "repairs": {}})

    def validate(self, document):
        require_keys(document, {"version", "revision", "events", "limits", "started_at_ms", "last_seen_ms",
            "status", "block_reason", "calls", "task_started_at_ms", "repairs"})
        strict_int(document["version"], "version", 1, 1)
        strict_int(document["revision"], "revision")
        if (not isinstance(document["events"], list)
                or any(not isinstance(value, str) or not value for value in document["events"])
                or len(document["events"]) != len(set(document["events"]))
                or document["revision"] != len(document["events"])):
            raise StateError("invalid event history")
        _validate_limits(document["limits"])
        strict_int(document["started_at_ms"], "started_at_ms")
        strict_int(document["last_seen_ms"], "last_seen_ms", document["started_at_ms"])
        if document["status"] not in {"READY", "BLOCKED"}:
            raise StateError("invalid subscription status")
        if document["block_reason"] is not None and document["block_reason"] not in BLOCK_REASONS:
            raise StateError("unknown block reason")
        if (document["status"] == "BLOCKED") != (document["block_reason"] is not None):
            raise StateError("status/reason mismatch")
        if not isinstance(document["calls"], dict) or not isinstance(document["task_started_at_ms"], dict):
            raise StateError("invalid calls/tasks")
        for task_id, started in document["task_started_at_ms"].items():
            require_text(task_id, "task_id", 80)
            strict_int(started, "task start", document["started_at_ms"], document["last_seen_ms"])
        for call_id, call in document["calls"].items():
            require_text(call_id, "call_id", 200)
            require_keys(call, {"task_id", "status", "started_at_ms", "finished_at_ms", "deadline_ms",
                "reserved_turns", "usage", "elapsed_seconds_reported", "block_reason"})
            if call["task_id"] not in document["task_started_at_ms"]:
                raise StateError("call task missing")
            strict_int(call["started_at_ms"], "call start", document["started_at_ms"], document["last_seen_ms"])
            strict_int(call["deadline_ms"], "deadline", call["started_at_ms"])
            strict_int(call["reserved_turns"], "reserved turns", 1)
            if call["status"] not in {"RUNNING", "COMPLETE", "BLOCKED"}:
                raise StateError("invalid call status")
            if call["finished_at_ms"] is not None:
                strict_int(call["finished_at_ms"], "call finish", call["started_at_ms"], document["last_seen_ms"])
            if call["elapsed_seconds_reported"] is not None:
                strict_int(call["elapsed_seconds_reported"], "elapsed_seconds_reported")
            if call["usage"] is not None:
                self._validate_usage(call["usage"])
            if call["status"] == "RUNNING" and (call["usage"] is not None or call["finished_at_ms"] is not None):
                raise StateError("running call contains terminal data")
            if call["status"] != "RUNNING" and call["finished_at_ms"] is None:
                raise StateError("finished call lacks finish time")
            if call["block_reason"] is not None and call["block_reason"] not in BLOCK_REASONS:
                raise StateError("invalid call block reason")
            if (call["status"] == "BLOCKED") != (call["block_reason"] is not None):
                raise StateError("call block reason mismatch")
        if sum(call["status"] == "RUNNING" for call in document["calls"].values()) > 1:
            raise StateError("only one model call may run")
        if not isinstance(document["repairs"], dict):
            raise StateError("invalid repairs")
        for task_id, count in document["repairs"].items():
            require_text(task_id, "task_id", 80)
            strict_int(count, "repairs", 0, document["limits"]["max_repairs"])

    @staticmethod
    def _validate_usage(usage):
        require_keys(usage, {"turns", "input_tokens", "output_tokens"})
        strict_int(usage["turns"], "turns", 1)
        for key in ("input_tokens", "output_tokens"):
            if usage[key] is not None:
                strict_int(usage[key], key)

    @staticmethod
    def usage_totals(document, task_id=None):
        calls = [call for call in document["calls"].values()
                 if task_id is None or call["task_id"] == task_id]
        return {
            "calls": len(calls),
            "turns": sum(max(call["reserved_turns"], call["usage"]["turns"] if call["usage"] else 0) for call in calls),
            "input_tokens": sum((call["usage"]["input_tokens"] or 0) if call["usage"] else 0 for call in calls),
            "output_tokens": sum((call["usage"]["output_tokens"] or 0) if call["usage"] else 0 for call in calls),
            "usage_unknown": any(call["status"] != "RUNNING" and (call["usage"] is None or
                call["usage"]["input_tokens"] is None or call["usage"]["output_tokens"] is None) for call in calls),
        }

    def _limit_reason(self, document, task_id, now, *, new_call=False, reserved_turns=0):
        limits = document["limits"]
        if now < document["last_seen_ms"]:
            return "clock_rollback"
        for scope, totals in (("batch", self.usage_totals(document)), ("task", self.usage_totals(document, task_id))):
            started = document["started_at_ms"] if scope == "batch" else document["task_started_at_ms"].get(task_id, now)
            if now - started >= limits[scope + "_wall_seconds"] * 1000:
                return "timeout"
            if totals["calls"] + int(new_call) > limits[scope + "_max_calls"]:
                return "limit_exceeded"
            if totals["turns"] + reserved_turns > limits[scope + "_max_turns"]:
                return "limit_exceeded"
            for token_type in ("input_tokens", "output_tokens"):
                cap = limits[scope + "_max_" + token_type]
                if cap is not None and totals["usage_unknown"]:
                    return "usage_unknown"
                if cap is not None and (totals[token_type] >= cap if new_call else totals[token_type] > cap):
                    return "limit_exceeded"
        return None

    def begin_call(self, call_id, task_id, *, expected_revision, reserved_turns=1):
        require_text(call_id, "call_id", 200)
        require_text(task_id, "task_id", 80)
        strict_int(reserved_turns, "reserved_turns", 1)
        def update(document):
            if document["status"] == "BLOCKED":
                raise LimitExceeded("subscription batch is blocked; no API fallback")
            if call_id in document["calls"] or any(c["status"] == "RUNNING" for c in document["calls"].values()):
                raise StateError("existing or unresolved model call; reconcile before continuing")
            now = self._now()
            reason = self._limit_reason(document, task_id, now, new_call=True, reserved_turns=reserved_turns)
            document["last_seen_ms"] = max(now, document["last_seen_ms"])
            if reason:
                document["status"], document["block_reason"] = "BLOCKED", reason
                return
            started = document["task_started_at_ms"].setdefault(task_id, now)
            limits = document["limits"]
            document["calls"][call_id] = {"task_id": task_id, "status": "RUNNING", "started_at_ms": now,
                "finished_at_ms": None, "deadline_ms": min(document["started_at_ms"] + limits["batch_wall_seconds"] * 1000,
                started + limits["task_wall_seconds"] * 1000), "reserved_turns": reserved_turns, "usage": None,
                "elapsed_seconds_reported": None, "block_reason": None}
        return self._mutate(expected_revision, "begin:" + call_id, update)

    def finish_call(self, call_id, usage, elapsed_seconds=None, *, expected_revision):
        if usage is not None:
            self._validate_usage(usage)
        if elapsed_seconds is not None:
            strict_int(elapsed_seconds, "elapsed_seconds")
        def update(document):
            call = document["calls"].get(call_id)
            if call is None or call["status"] != "RUNNING":
                raise StateError("unknown or finished model call")
            now = self._now()
            clock_rollback = now < document["last_seen_ms"]
            document["last_seen_ms"] = max(now, document["last_seen_ms"])
            call.update(status="COMPLETE", finished_at_ms=max(now, call["started_at_ms"]), usage=usage,
                        elapsed_seconds_reported=elapsed_seconds)
            reason = "clock_rollback" if clock_rollback else self._limit_reason(document, call["task_id"], now)
            if now >= call["deadline_ms"]:
                reason = "timeout"
            if usage is not None and usage["turns"] > call["reserved_turns"]:
                reason = "limit_exceeded"
            if reason:
                document["status"], document["block_reason"] = "BLOCKED", reason
                call["status"], call["block_reason"] = "BLOCKED", reason
        return self._mutate(expected_revision, "finish:" + call_id, update)

    def block(self, call_id, reason, *, expected_revision):
        if reason not in BLOCK_REASONS:
            raise StateError("unknown stop reason")
        def update(document):
            call = document["calls"].get(call_id)
            if call is None or call["status"] != "RUNNING":
                raise StateError("unknown or finished call")
            now = max(self._now(), document["last_seen_ms"])
            document["last_seen_ms"] = now
            document["status"], document["block_reason"] = "BLOCKED", reason
            call.update(status="BLOCKED", block_reason=reason, finished_at_ms=now)
        return self._mutate(expected_revision, "block:" + call_id, update)

    def record_repair(self, task_id, *, expected_revision, event_id):
        require_text(task_id, "task_id", 80)
        def update(document):
            count = document["repairs"].get(task_id, 0)
            if document["status"] == "BLOCKED" or count >= document["limits"]["max_repairs"]:
                raise LimitExceeded("repair limit reached or batch blocked")
            document["repairs"][task_id] = count + 1
        return self._mutate(expected_revision, event_id, update)
