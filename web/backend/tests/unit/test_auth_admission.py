"""Actual Windows byte-range locks; promotion releases only its queue slot."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import threading

from classroom_web.infrastructure.auth_admission import HashAdmission
from classroom_web.infrastructure.ws_admission import WebSocketAdmission
from classroom_web.infrastructure.auth_crypto import Credentials
from classroom_web.domain.auth import AuthRejected
import secrets,pytest


def test_promotion_returns_queue_capacity_while_actual_hash_remains_running(tmp_path):
    gate=HashAdmission(tmp_path/'slots');started=[threading.Event(),threading.Event()];finish=[threading.Event(),threading.Event()]
    third_started=threading.Event();third_finish=threading.Event()
    def body(i):started[i].set();assert finish[i].wait(3)
    def third():third_started.set();assert third_finish.wait(3)
    with ThreadPoolExecutor(max_workers=3) as workers:
        first=workers.submit(gate,lambda:body(0));second=workers.submit(gate,lambda:body(1))
        try:
            assert all(e.wait(1) for e in started)
            queued=workers.submit(gate,third)
            finish[0].set();assert third_started.wait(1)
            with ExitStack() as held:
                assert all(held.enter_context(gate._claim(range(2,10))) for _ in range(8))
                assert not held.enter_context(gate._claim(range(2,10)))
                assert not held.enter_context(gate._claim(range(2)))
        finally:
            for e in finish:e.set()
            third_finish.set()
        first.result();second.result();queued.result()


def test_simultaneous_initializers_publish_only_complete_slot_files(tmp_path):
    barrier=threading.Barrier(16);path=tmp_path/'simultaneous'
    def initialize(_):barrier.wait(timeout=3);return HashAdmission(path).path.stat().st_size
    with ThreadPoolExecutor(max_workers=16) as workers:assert list(workers.map(initialize,range(16)))==[10]*16


def test_two_adapters_share_ws_total_pending_and_same_source_limits(tmp_path):
    crypto=Credentials(encryption_key=secrets.token_bytes(32),commitment_key=secrets.token_bytes(32))
    first=WebSocketAdmission(tmp_path,crypto);second=WebSocketAdmission(tmp_path,crypto)
    with ExitStack() as held:
        for _ in range(8):held.enter_context(first.connection('synthetic-source'))
        with pytest.raises(AuthRejected):held.enter_context(second.connection('synthetic-source'))
        for i in range(8):held.enter_context(first.connection('another-source-'+str(i)))
        with pytest.raises(AuthRejected):held.enter_context(second.connection('new-source'))
    with ExitStack() as held:
        for i in range(64):
            authenticated=held.enter_context((first if i%2 else second).connection('synthetic-source'));authenticated()
        with pytest.raises(AuthRejected):held.enter_context(second.connection('another-source'))


@pytest.mark.parametrize('queued',[False,True])
def test_slow_slot_acquisition_past_deadline_never_starts_hash_and_releases_slots(tmp_path,monkeypatch,queued):
    from contextlib import contextmanager
    from types import SimpleNamespace
    import classroom_web.infrastructure.auth_admission as admission
    clock=[100.0];calls=[];events=[]
    monkeypatch.setattr(admission,'time',SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda delay:None))
    class DelayedAdmission(HashAdmission):
        attempts=0
        @contextmanager
        def _claim(self,indices):
            indices=tuple(indices)
            if indices==tuple(range(2)):
                self.attempts+=1
                if queued and self.attempts==1:
                    yield False
                    return
                # Retain the real OS slot, while simulating an I/O operation
                # that finished after the absolute one-second queue deadline.
                with super()._claim(indices) as acquired:
                    assert acquired
                    clock[0]+=1.001
                    yield acquired
            else:
                with super()._claim(indices) as acquired:yield acquired
    gate=DelayedAdmission(tmp_path/'slow-slot',observer=lambda stage,seconds:events.append(stage))
    with pytest.raises(AuthRejected) as error:gate(lambda:calls.append('hash'))
    assert error.value.code=='RATE_LIMITED' and not calls and events==['queue_timeout']
    assert HashAdmission(tmp_path/'slow-slot')(lambda:'slot released')=='slot released'
