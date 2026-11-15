"""Trusted ASR gate; candidate discovery and controller assertions run separately.

The parent must validate the structured terminal record, not just process exit.
This is not a proof against arbitrary hostile Python executing in the interpreter;
the independent trusted phase never imports the candidate's test modules.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import struct
import unittest

MARKER = "OVERNIGHT_TEST_RESULT "
TRUSTED_TESTS = 4


def passing_summary(summary, *, stage=None, nonce=None, binding=None):
    if not isinstance(summary, dict):
        return False
    if (type(summary.get("schema_version")) is not int or summary.get("schema_version") != 1
            or summary.get("stage") not in {"candidate", "trusted"}
            or stage is not None and summary.get("stage") != stage
            or nonce is not None and summary.get("nonce") != nonce
            or binding is not None and summary.get("candidate_binding") != binding
            or summary.get("complete") is not True):
        return False
    count = summary.get("discovered")
    if (type(count) is not int or count <= 0 or type(summary.get("tests_run")) is not int
            or summary.get("tests_run") != count or type(summary.get("completed")) is not int
            or summary.get("completed") != count
            or summary.get("stage") == "trusted" and count != TRUSTED_TESTS):
        return False
    return all(type(summary.get(key)) is int and summary[key] == 0 for key in
               ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"))


def parse_summary(stdout, *, stage, nonce, binding):
    records = [line[len(MARKER):] for line in stdout.splitlines() if line.startswith(MARKER)]
    if len(records) != 1:
        raise ValueError("test session has missing or duplicate completion evidence")
    try:
        result = json.loads(records[0])
    except (ValueError, TypeError) as error:
        raise ValueError("test session completion evidence is truncated or malformed") from error
    if not passing_summary(result, stage=stage, nonce=nonce, binding=binding):
        raise ValueError("test session did not complete every discovered test without failures or skips")
    return result


def require_evidence(result, binding):
    if (not isinstance(result, dict) or result.get("passed") is not True
            or result.get("candidate_binding") != binding
            or not isinstance(result.get("verified_checkout"), dict)
            or result["verified_checkout"].get("head_sha") != binding.get("head_sha")):
        raise ValueError("passing tests require a verified tracked-only checkout bound to the candidate")
    sessions = result.get("sessions")
    if not isinstance(sessions, dict) or set(sessions) != {"candidate", "trusted"}:
        raise ValueError("both candidate and trusted test sessions are mandatory")
    for stage, session in sessions.items():
        if (not isinstance(session, dict) or session.get("passed") is not True
                or session.get("exit_code") != 0
                or not passing_summary(session.get("summary"), stage=stage, binding=binding)):
            raise ValueError("missing complete passing test-session evidence")


def _ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _ids(test)
        else:
            yield test.id()


class SessionResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.completed_ids = []

    def stopTest(self, test):
        self.completed_ids.append(test.id())
        super().stopTest(test)


class TrustedAssertions(unittest.TestCase):
    """Controller-owned acceptance checks independent of candidate test claims."""
    @staticmethod
    def config(script=None, **extra):
        config = dict(durable_audio=True, budget_permit=True, consent=True, dispatch_intent=True,
                      class_session_id="00000000-0000-4000-8000-000000000001",
                      capture_epoch_id="00000000-0000-4000-8000-000000000002",
                      stream_id="00000000-0000-4000-8000-000000000003", script=script or [])
        return dict(config, **extra)

    @staticmethod
    def final():
        return dict(kind="final", segment_id="00000000-0000-4000-8000-000000000004",
                    revision=1, sequence=0, start_sample=0, end_sample=16,
                    text="synthetic", timestamp_source="capture_mapping")

    @staticmethod
    def frame():
        pcm = b"\x01\x00" * 16
        return dict(pcm=pcm, sample_rate=16000, channels=1, encoding="signed16le",
                    start_sample=0, end_sample=16, sample_count=16,
                    byte_length=len(pcm), sha256=hashlib.sha256(pcm).hexdigest())

    def setUp(self):
        import inspect
        from classroom_web.asr_sim import Simulator, ProtocolError
        # A dependency directory must never substitute an older/worker module.
        expected = (WORKSPACE / "web/backend/classroom_web/asr_sim/simulator.py").resolve()
        for value in (Simulator, ProtocolError):
            self.assertEqual(Path(inspect.getfile(value)).resolve(), expected)
        self.Simulator, self.ProtocolError = Simulator, ProtocolError

    def test_required_start_guards(self):
        for key in ("durable_audio", "budget_permit", "consent", "dispatch_intent"):
            with self.subTest(guard=key), self.assertRaises(self.ProtocolError):
                self.Simulator().start(self.config(**{key: False}))
        for prefix in (b"RIFF", b"RIFX", b"RF64"):
            sim = self.Simulator()
            sim.start(self.config())
            frame = dict(self.frame(), pcm=prefix, end_sample=2, sample_count=2,
                         byte_length=4, sha256=hashlib.sha256(prefix).hexdigest())
            sim.send(frame)
        wave = b"RIFF" + struct.pack("<I", 38) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) + b"data" + struct.pack("<I", 2) + b"\0\0"
        sim = self.Simulator()
        sim.start(self.config())
        bad = dict(self.frame(), pcm=wave, end_sample=len(wave) // 2, sample_count=len(wave) // 2,
                   byte_length=len(wave), sha256=hashlib.sha256(wave).hexdigest())
        with self.assertRaises(self.ProtocolError):
            sim.send(bad)

    def test_final_commit_and_frozen_schema(self):
        from jsonschema import Draft202012Validator, FormatChecker
        sim = self.Simulator()
        sim.start(self.config([self.final(), self.final(), {"kind": "terminal"}], commit_failures=1))
        sim.send(self.frame())
        sim.finish()
        self.assertEqual(sim.events(), [])
        self.assertEqual(sim.state, "draining")
        events = sim.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "transcript.final")
        schema = json.loads((WORKSPACE / "docs/web-alpha/architecture/contracts/events.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        validator.validate(events[0])
        invalid = deepcopy(events[0])
        invalid["event_id"] = "not-a-uuid"
        self.assertTrue(list(validator.iter_errors(invalid)))
        self.assertEqual(sim.events(), [])
        self.assertEqual(sim.state, "succeeded")
        self.assertEqual(sim.pending_bytes, 0)

    def test_unknown_never_replays_sent_audio(self):
        sim = self.Simulator()
        receipt = sim.start(self.config([{"kind": "lost"}]))
        sim.send(self.frame())
        sim.finish()
        sim.events()
        self.assertEqual(sim.state, "unknown")
        self.assertEqual(sim.pending_bytes, 0)
        self.assertEqual(sim.query(receipt)["status"], "unsupported")
        sim.start(self.config(stream_id="00000000-0000-4000-8000-000000000005"))
        with self.assertRaises(self.ProtocolError):
            sim.send(self.frame())

    def test_terminal_without_final_never_succeeds(self):
        sim = self.Simulator()
        receipt = sim.start(self.config([{"kind": "terminal"}],
            query_result={"proven": True, "script": [self.final(), {"kind": "terminal"}]}))
        sim.send(self.frame())
        sim.finish()
        self.assertEqual(sim.events(), [])
        self.assertEqual(sim.state, "unknown")
        self.assertEqual(sim.pending_bytes, 0)
        self.assertEqual(sim.query(receipt)["status"], "succeeded")


def main():
    global WORKSPACE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("candidate", "trusted"), required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--deps", type=Path, required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--patch", required=True)
    args = parser.parse_args()
    WORKSPACE = args.workspace.resolve(strict=True)
    # -I isolates interpreter startup; only these controller-chosen roots are added.
    sys.path[:0] = [str(WORKSPACE / "web/backend"), str(args.deps.resolve(strict=True))]
    if args.stage == "candidate":
        suite = unittest.defaultTestLoader.discover(str(WORKSPACE / "web/backend/tests/asr_sim"), pattern="test_*.py")
    else:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(TrustedAssertions)
    expected = list(_ids(suite))
    result = unittest.TextTestRunner(verbosity=2, resultclass=SessionResult).run(suite)
    summary = dict(schema_version=1, stage=args.stage, nonce=args.nonce,
                   candidate_binding={"head_sha": args.head, "patch_sha256": args.patch},
                   complete=sorted(expected) == sorted(result.completed_ids),
                   discovered=len(expected), tests_run=result.testsRun, completed=len(result.completed_ids),
                   failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                   expected_failures=len(result.expectedFailures), unexpected_successes=len(result.unexpectedSuccesses))
    print(MARKER + json.dumps(summary, sort_keys=True), flush=True)
    return 0 if passing_summary(summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
