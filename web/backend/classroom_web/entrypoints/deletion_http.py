"""Fail-closed admission while deletion state is unverified.

A database must not serve private data before the independent deletion ledger has been
read and every recorded revocation is present locally: a copy restored from a backup
would otherwise show objects that were deleted after the backup was taken.
"""
from uuid import uuid4

from ..domain.auth import AuthRejected


class DeletionGate:
    def __init__(self, app, *, deletion, failure, prefix):
        self.app, self.deletion, self.failure, self.prefix = app, deletion, failure, prefix + '/'

    async def __call__(self, scope, receive, send):
        if (scope['type'] in ('http', 'websocket') and scope.get('path', '').startswith(self.prefix)
                and not self.deletion.ready):
            if scope['type'] == 'http':
                response = self.failure(AuthRejected('SERVICE_DISABLED', 503), str(uuid4()))
                await response(scope, receive, send)
            else:
                await receive()
                await send({'type': 'websocket.close', 'code': 1013})
            return
        await self.app(scope, receive, send)
