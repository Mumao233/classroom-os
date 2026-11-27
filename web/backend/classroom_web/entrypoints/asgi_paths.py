"""Match ASGI scopes against API routes, with or without a mount prefix.

Starlette routes relative to ``root_path``. ASGI servers may send ``path`` with or
without that prefix, so security middleware considers both forms and matches when
either names the route: over-matching only gates or redacts more.
"""


def route_paths(scope):
    path = scope.get('path', '')
    root = scope.get('root_path', '').rstrip('/')
    candidates = {path}
    if root and path.startswith(root + '/'):
        candidates.add(path[len(root):])
    return candidates


def route_path(scope):
    """The path Starlette routes on: ``path`` without a leading ``root_path`` mount prefix."""
    path = scope.get('path', '')
    root = scope.get('root_path', '').rstrip('/')
    return path[len(root):] if root and path.startswith(root + '/') else path


def path_is(scope, route):
    return any(candidate.rstrip('/') == route for candidate in route_paths(scope))


def path_under(scope, prefix):
    return any(candidate.startswith(prefix) for candidate in route_paths(scope))
