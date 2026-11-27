"""Fail-closed admission while deletion state is unverified.

A database must not serve private data before the independent deletion ledger has been
read and every recorded revocation is present locally: a copy restored from a backup
would otherwise show objects that were deleted after the backup was taken.
"""
from uuid import uuid4

from ..domain.auth import AuthRejected
from .asgi_paths import path_under


class DeletionGate:
    def __init__(self, app, *, deletion, failure, prefix):
        self.app, self.deletion, self.failure, self.prefix = app, deletion, failure, prefix + '/'

    async def __call__(self, scope, receive, send):
        # Mounted deployments prefix `path` with root_path; the gate must still apply.
        if (scope['type'] in ('http', 'websocket') and path_under(scope, self.prefix)
                and not self.deletion.ready):
            if scope['type'] == 'http':
                response = self.failure(AuthRejected('SERVICE_DISABLED', 503), str(uuid4()))
                await response(scope, receive, send)
            else:
                await receive()
                await send({'type': 'websocket.close', 'code': 1013})
            return
        await self.app(scope, receive, send)
