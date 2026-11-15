"""Transport failure during COMMIT is unknown; execution failure is not replay permission."""
from contextlib import contextmanager
from collections import deque
import pytest,sqlalchemy as sa
from classroom_web.infrastructure.auth_repository import PgAuthRepository
from classroom_web.domain.auth import AuthRejected


@pytest.mark.parametrize('during_commit',[False,True])
def test_connection_loss_preserves_unknown_commit_boundary_without_secret_details(during_commit):
    error=sa.exc.OperationalError('fixed SQL',None,ConnectionError('PRIVATE_SENTINEL'),connection_invalidated=True)
    class Connection:
        def scalar(self,statement,*args):
            if 'pg_roles' in str(statement):return True
            if not during_commit:raise error
            return {}
        def execute(self,*args):pass
    class Engine:
        @contextmanager
        def begin(self):
            yield Connection()
            if during_commit:raise error
    repo=PgAuthRepository.__new__(PgAuthRepository);repo.engines={'session':Engine()};repo.faults=deque(maxlen=32)
    with pytest.raises(AuthRejected) as caught:repo.call('protocol_context',{})
    assert caught.value.code==('RESULT_UNKNOWN' if during_commit else 'STORAGE_UNAVAILABLE')
    assert 'PRIVATE' not in str(caught.value) and 'PRIVATE' not in str(repo.faults)


@pytest.mark.parametrize('during_commit',[False,True])
def test_identity_connection_loss_during_commit_is_an_unknown_outcome(during_commit):
    from classroom_web.infrastructure.identity_repository import PgIdentityRepository
    error=sa.exc.OperationalError('fixed SQL',None,ConnectionError('PRIVATE_SENTINEL'),connection_invalidated=True)
    class Connection:
        def scalar(self,statement,*args):return True
        def execute(self,*args):
            if not during_commit and 'statement_timeout' in str(args[0]):raise error
    class Engine:
        @contextmanager
        def begin(self):
            yield Connection()
            if during_commit:raise error
    repo=PgIdentityRepository.__new__(PgIdentityRepository);repo.engine=Engine()
    # Registration, verification and resend all commit through this transaction helper.
    if during_commit:
        with pytest.raises(AuthRejected) as caught:
            with repo._transaction():pass
        assert caught.value.code=='RESULT_UNKNOWN' and caught.value.status==503 and 'PRIVATE' not in str(caught.value)
    else:
        with pytest.raises(sa.exc.OperationalError):
            with repo._transaction():pass
