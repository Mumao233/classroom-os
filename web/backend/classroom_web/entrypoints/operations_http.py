"""Register PR-11c routes through the shared authenticated HTTP boundary."""
from ..domain.auth import AuthRejected
from .asgi_paths import path_is


# 0.4.0 (PR #40, PR-11b admin writes) leaves these four operations, their schemas and error
# codes unchanged; its canonical Idempotency-Key is the UUID v4 form these routes already
# require. create_app's own version gate is widened by that contract change itself.
SUPPORTED_CONTRACTS = ('0.3.0', '0.4.0')

ROUTES = {
    '/search': ('get', 'search'),
    '/notifications': ('get', 'listNotifications'),
    '/notifications/{notification_id}/read': ('post', 'readNotification'),
    '/feedback': ('post', 'submitFeedback'),
}


class SearchQueryRedaction:
    """Leave the query available to the handler and redact it before access logging."""

    def __init__(self, app, *, path):
        self.app, self.path = app, path

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not path_is(scope, self.path):
            await self.app(scope, receive, send)
            return

        async def redacted_send(message):
            if message['type'] == 'http.response.start':
                scope['query_string'] = b''
            await send(message)

        try:
            await self.app(scope, receive, redacted_send)
        finally:
            scope['query_string'] = b''


class OperationsRoutes:
    def __init__(self, service, auth, contract, deletion, business, asr, filetrans, review):
        self.service = service
        self.contract = contract
        self.paths = ROUTES if service is not None else {}
        if service is None:
            return
        if (contract['info']['version'] not in SUPPORTED_CONTRACTS or deletion is None
                or service.auth is not auth or service.database is not deletion.database
                or deletion.auth is not auth):
            raise ValueError('PR-11c requires contract 0.3.0 or 0.4.0 and the shared authentication and deletion gate')
        if service.database.cursor_secret is None:
            raise ValueError('PR-11c requires the shared stable owner cursor secret')
        for reader in (business, asr, filetrans, review):
            if reader is not None and (reader.auth is not auth or reader.database is not service.database):
                raise ValueError('PR-11c readers must share authentication and database')
        self.business = business
        self.asr = asr
        self.filetrans = filetrans
        self.review = review

    def register(self, app, prefix, endpoint):
        for path, (method, operation_id) in self.paths.items():
            operation = self.contract['paths'][path][method]
            if operation['operationId'] != operation_id:
                raise ValueError('PR-11c operation does not match the frozen contract')
            app.add_api_route(prefix + path, endpoint(path, method, operation),
                              methods=[method.upper()], operation_id=operation_id)

    def dispatch(self, operation, ctx, body, key, params):
        file_reads = self.filetrans is not None and getattr(
            self.filetrans.capture.store, 'synthetic_loopback', False)
        readers = {
            'courses': self.business is not None,
            'classrooms': file_reads,
            'transcripts': file_reads if self.filetrans is not None else self.asr is not None,
            'reviews': self.review is not None and self.review.config.get('enabled', False),
        }
        return self.service.dispatch(operation, ctx, body, key, params, readers)

    @staticmethod
    def query(request, operation, method, check):
        if method != 'get':
            return {}
        if len(request.scope['query_string']) > 4096:
            raise AuthRejected('VALIDATION_FAILED', 422)
        fields = {p['name']: p for p in operation['parameters'] if p.get('in') == 'query'}
        values = {}
        for name, value in request.query_params.multi_items():
            if name in values or name not in fields:
                raise AuthRejected('VALIDATION_FAILED', 422)
            schema = fields[name]['schema']
            if schema['type'] == 'integer':
                if not value.isascii() or not value.isdecimal() or len(value) > 3:
                    raise AuthRejected('VALIDATION_FAILED', 422)
                value = int(value)
            check(schema, value)
            values[name] = value
        if any(field.get('required') and name not in values for name, field in fields.items()):
            raise AuthRejected('VALIDATION_FAILED', 422)
        return values
