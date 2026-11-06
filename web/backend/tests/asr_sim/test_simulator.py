import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'web/backend'))
from classroom_web.asr_sim import Simulator, ProtocolError


def uid(n):
    return '00000000-0000-4000-8000-%012d' % n


def config(script=None, **extra):
    value = dict(durable_audio=True, budget_permit=True, consent=True,
                 dispatch_intent=True, class_session_id=uid(1),
                 capture_epoch_id=uid(2), stream_id=uid(3), script=script or [])
    value.update(extra)
    return value


def frame(start=0, samples=1600):
    pcm = b'\x01\x00' * samples
    return dict(pcm=pcm, sample_rate=16000, channels=1, encoding='signed16le',
                start_sample=start, end_sample=start + samples,
                sample_count=samples, byte_length=len(pcm),
                sha256=hashlib.sha256(pcm).hexdigest())


def final(segment=4, revision=1, **extra):
    value = dict(kind='final', segment_id=uid(segment), revision=revision,
                 sequence=0, start_sample=0, end_sample=1600, text='same text',
                 timestamp_source='capture_mapping')
    value.update(extra)
    return value


class SimulatorTests(unittest.TestCase):
    def running(self, script, **extra):
        sim = Simulator()
        sim.start(config(script, **extra))
        sim.send(frame())
        return sim

    def test_guards_and_lifecycle(self):
        for guard in ('durable_audio', 'budget_permit', 'consent', 'dispatch_intent'):
            for invalid in (False, 1, None):
                sim = Simulator()
                with self.assertRaises(ProtocolError):
                    sim.start(config(**{guard: invalid}))
                with self.assertRaises(ProtocolError):
                    sim.send(frame())
        sim = self.running([final(), {'kind': 'terminal'}])
        self.assertEqual(sim.finish()['state'], 'draining')
        with self.assertRaises(ProtocolError):
            sim.send(frame(1600))
        self.assertEqual(len(sim.events()), 1)
        self.assertEqual(sim.state, 'succeeded')
        self.assertEqual(sim.pending_bytes, 0)
        self.assertEqual(sim.events(), [])

    def test_finish_without_terminal_and_commit_failure(self):
        sim = self.running([final(), {'kind': 'terminal'}], commit_failures=1)
        expected = sim._payload(final())
        sim.finish()
        self.assertEqual(sim.events(), [])
        self.assertEqual(sim.state, 'draining')
        self.assertEqual(sim.events(), [expected])
        other = self.running([final()])
        other.finish()
        other.events()
        self.assertEqual(other.state, 'draining')
        active = self.running([{'kind': 'terminal'}])
        self.assertEqual(active.events(), [])
        self.assertEqual(active.state, 'streaming')

    def test_pcm_validation_atomic(self):
        mutations = [dict(pcm=b'x'), dict(sample_count=True), dict(start_sample=-1),
                     dict(end_sample=0), dict(byte_length=2), dict(sha256='bad'),
                     dict(sample_rate=48000), dict(channels=True), dict(encoding='wav'),
                     dict(pcm=b'RIFF' + b'\0' * 3196), dict(end_sample=False)]
        for change in mutations:
            sim = Simulator()
            sim.start(config())
            bad = frame()
            bad.update(change)
            with self.subTest(change=tuple(change)):
                with self.assertRaises(ProtocolError):
                    sim.send(bad)
                self.assertEqual(sim.pending_bytes, 0)
                sim.send(frame())
        sim = self.running([])
        for start in (0, 1599, 1601):
            with self.assertRaises(ProtocolError):
                sim.send(frame(start))

    def test_unknown_reconnect_no_replay(self):
        sim = self.running([{'kind': 'lost'}])
        receipt = sim.finish()
        self.assertEqual(sim.events(), [])
        self.assertEqual(sim.state, 'unknown')
        self.assertEqual(sim.query(receipt), {'status': 'unsupported'})
        self.assertEqual(sim.state, 'unknown')
        with self.assertRaises(ProtocolError):
            sim.start(config())
        sim.start(config(stream_id=uid(8)))
        with self.assertRaises(ProtocolError):
            sim.send(frame())
        sim.send(frame(1600))
        self.assertEqual(sim.pending_bytes, 3200)

    def test_unknown_proof_and_failed_commit(self):
        proof = dict(proven=True, script=[final(), {'kind': 'terminal'}])
        sim = self.running([{'kind': 'lost'}], query_result=proof, commit_failures=1)
        receipt = sim.finish()
        sim.events()
        self.assertEqual(sim.query(receipt), {'status': 'unknown', 'events': []})
        result = sim.query(receipt)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(sim.query(receipt), {'status': 'succeeded'})
        with self.assertRaises(ProtocolError):
            sim.query({'stream_id': uid(99)})
        with self.assertRaises(ProtocolError):
            Simulator().start(config(query_result=dict(proven=False, script=[])))

    def test_stable_identity_revision_and_quarantine(self):
        script = [final(revision=2), final(revision=2), final(revision=1),
                  final(revision=2, text='conflict'), final(segment=5),
                  {'kind': 'terminal'}]
        sim = self.running(script)
        sim.finish()
        events = sim.events()
        self.assertEqual(len(events), 2)
        self.assertEqual([e['data']['revision'] for e in events], [2, 1])
        self.assertNotEqual(events[0]['event_id'], events[1]['event_id'])
        self.assertEqual(len(sim.quarantine), 1)
        self.assertEqual(sim.state, 'succeeded')

    def test_contract_payloads_and_format_checker(self):
        from jsonschema import Draft202012Validator, FormatChecker
        partial = final(kind='partial')
        partial.pop('timestamp_source')
        sim = self.running([partial, final(), final(segment=5, timestamp_source='provider_segment'),
                            {'kind': 'terminal'}])
        sim.finish()
        payloads = sim.events()
        schema = json.loads((ROOT / 'docs/web-alpha/architecture/contracts/events.schema.json').read_text(encoding='utf-8'))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for payload in payloads:
            validator.validate(payload)
        self.assertEqual(payloads[0]['connection_seq'], 0)
        self.assertNotIn('event_id', payloads[0])
        self.assertNotIn('cursor', payloads[0])
        bad = copy.deepcopy(payloads[-1])
        bad['event_id'] = 'not-a-uuid'
        self.assertTrue(list(validator.iter_errors(bad)))
        bad = copy.deepcopy(payloads[-1])
        bad['simulation'] = True
        self.assertTrue(list(validator.iter_errors(bad)))

    def test_batch_validation_does_not_lose_committed_finals(self):
        sim = self.running([final(), final(segment=5, end_sample=3200),
                            {'kind': 'terminal'}])
        expected = sim._payload(final())
        for _ in range(2):
            with self.assertRaisesRegex(ProtocolError, 'outside sent audio'):
                sim.events()
            self.assertEqual(sim.state, 'streaming')
            self.assertEqual(sim._position, 0)
            self.assertEqual(sim._seen, {})
            self.assertEqual(sim._revisions, {})
            self.assertEqual(sim.pending_bytes, 3200)
        # Supply missing synthetic audio; no final was committed or lost by
        # the rejected batches, and the first final keeps its stable identity.
        sim.send(frame(1600))
        sim.finish()
        output = sim.events()
        self.assertEqual(output[0], expected)
        self.assertEqual(len(output), 2)
        self.assertEqual(sim.state, 'succeeded')
        self.assertEqual(sim.events(), [])

    def test_reconciliation_validation_preserves_unknown(self):
        proof = dict(proven=True, script=[final(), final(segment=5, end_sample=3200),
                                        {'kind': 'terminal'}])
        sim = self.running([{'kind': 'lost'}], query_result=proof)
        receipt = sim.finish()
        sim.events()
        for _ in range(2):
            with self.assertRaisesRegex(ProtocolError, 'outside sent audio'):
                sim.query(receipt)
            self.assertEqual(sim.state, 'unknown')
            self.assertEqual(sim._seen, {})
            self.assertEqual(sim._position, 0)
            self.assertEqual(sim.pending_bytes, 0)
            with self.assertRaises(ProtocolError):
                sim.send(frame(1600))
        # Test-only injection of corrected positive evidence. There is no
        # public proof replacement API or resend during reconciliation.
        sim._query_result['script'][1]['end_sample'] = 1600
        result = sim.query(receipt)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(len(result['events']), 2)
        self.assertEqual(result['events'][0], sim._payload(final()))

    def test_segment_coverage_across_frames_and_boundaries(self):
        sim = self.running([final(start_sample=800, end_sample=3200),
                            {'kind': 'terminal'}])
        sim.send(frame(1600))
        sim.finish()
        self.assertEqual(len(sim.events()), 1)
        self.assertEqual(sim.state, 'succeeded')
        for start, end in ((0, 3201), (0, 1600)):
            sim = Simulator()
            sim.start(config([final(start_sample=start, end_sample=end)]))
            sim.send(frame(1600))
            with self.assertRaises(ProtocolError):
                sim.events()
            self.assertEqual(sim._seen, {})

    def test_partial_final_revision_ordering(self):
        def partial(revision):
            action = final(kind='partial', revision=revision)
            del action['timestamp_source']
            return action
        sim = self.running([final(revision=2), partial(1), partial(3),
                            partial(2), final(revision=2), final(revision=3),
                            {'kind': 'terminal'}])
        sim.finish()
        events = sim.events()
        self.assertEqual([(e['type'], e['data']['revision']) for e in events],
                         [('transcript.final', 2), ('transcript.partial', 3),
                          ('transcript.final', 3)])
        self.assertEqual(events[1]['connection_seq'], 0)
        self.assertNotIn('event_id', events[1])
        self.assertNotIn('cursor', events[1])
        self.assertEqual(sim.state, 'succeeded')

    def test_bounds_and_cleanup(self):
        for script in ([final()] * 129, [final(text='x' * 20001)],
                       [final(revision=True)], [final(start_sample=-1)],
                       [{'kind': 'terminal'}, final()]):
            with self.assertRaises(ProtocolError):
                Simulator().start(config(script))
        sim = Simulator()
        sim.start(config([{'kind': 'lost'}]))
        with self.assertRaises(ProtocolError):
            sim.send(frame(samples=16001))
        for i in range(4):
            sim.send(frame(i * 16000, 16000))
        with self.assertRaises(ProtocolError):
            sim.send(frame(64000, 1))
        self.assertEqual(sim.pending_bytes, 128000)
        sim.events()
        self.assertEqual(sim.pending_bytes, 0)
        sim = Simulator()
        sim.start(config())
        for i in range(128):
            sim.send(frame(i, 1))
        with self.assertRaises(ProtocolError):
            sim.send(frame(128, 1))

    def test_partial_cannot_suppress_final_commit_or_complete_stream(self):
        partial = final(kind='partial', revision=3)
        del partial['timestamp_source']
        sim = self.running([partial, final(revision=2), {'kind': 'terminal'}],
                           commit_failures=1)
        expected = sim._payload(final(revision=2))
        sim.finish()
        output = sim.events()
        self.assertEqual([e['type'] for e in output], ['transcript.partial'])
        self.assertEqual(sim._commit_failures, 0)
        self.assertEqual(sim._seen, {})
        self.assertEqual(sim._revisions, {})
        self.assertEqual(sim.state, 'draining')
        self.assertEqual(sim.events(), [expected])
        self.assertEqual(sim._revisions, {uid(4): 2})
        self.assertEqual(len(sim._seen), 1)
        # Revision 3 still has only transient evidence, despite final 2 commit.
        self.assertEqual(sim.state, 'draining')
        self.assertEqual(sim.events(), [])
        self.assertEqual(sim.state, 'draining')

    def test_partial_only_segment_prevents_success(self):
        partial = final(segment=5, kind='partial')
        del partial['timestamp_source']
        sim = self.running([final(), partial, {'kind': 'terminal'}])
        sim.finish()
        self.assertEqual(len(sim.events()), 2)
        self.assertEqual(len(sim._seen), 1)
        self.assertEqual(sim.state, 'draining')

    def test_terminal_alone_cannot_prove_commit(self):
        sim = self.running([{'kind': 'terminal'}])
        sim.finish()
        self.assertEqual(sim.events(), [])
        self.assertEqual(sim.state, 'draining')
        self.assertEqual(sim._seen, {})

    def test_terminal_only_query_remains_unknown(self):
        partial = final(kind='partial')
        del partial['timestamp_source']
        for script in ([{'kind': 'terminal'}],
                       [partial, {'kind': 'terminal'}]):
            sim = self.running([{'kind': 'lost'}],
                               query_result=dict(proven=True, script=script))
            receipt = sim.finish()
            sim.events()
            for _ in range(2):
                result = sim.query(receipt)
                self.assertEqual(result['status'], 'unknown')
                self.assertEqual(sim._seen, {})
                self.assertEqual(sim.pending_bytes, 0)
            with self.assertRaises(ProtocolError):
                sim.send(frame())

    def test_query_can_confirm_already_committed_final(self):
        sim = self.running([final(), {'kind': 'lost'}], query_result=dict(
            proven=True, script=[{'kind': 'terminal'}]))
        receipt = sim.finish()
        self.assertEqual(len(sim.events()), 1)
        self.assertEqual(sim.state, 'unknown')
        self.assertEqual(sim.query(receipt), {'status': 'succeeded', 'events': []})
        self.assertEqual(len(sim._seen), 1)

    def test_known_failure_and_input_copy(self):
        script = [final(), {'kind': 'failure'}]
        sim = self.running(script)
        script[0]['text'] = 'modified externally'
        output = sim.events()
        self.assertEqual(output[0]['data']['text'], 'same text')
        self.assertEqual(sim.state, 'failed')
        self.assertEqual(sim.pending_bytes, 0)
        with self.assertRaises(ProtocolError):
            sim.send(frame(1600))


if __name__ == '__main__':
    unittest.main()
