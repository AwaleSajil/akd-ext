"""ASGI entrypoint for running the akd-ext MCP server on AWS Lambda.

The AWS Lambda Web Adapter runs this as a normal HTTP app (via uvicorn), so no
Lambda-specific handler is needed. ``stateless_http=True`` makes every MCP request
independent (no long-lived SSE session), which is what Lambda's request/response
model requires.

AWS credentials are taken from the Lambda execution role automatically (boto3's
default chain), so NO AWS_* key env vars should be set on the function.

Access control: if the env var ``MCP_AUTH_TOKEN`` is set, every HTTP request must
carry ``Authorization: Bearer <that token>`` or it is rejected with 401. This lets
the Function URL be public (a plain URL clients hit with a token), while the server
itself stays closed. If the var is unset, no check is applied (useful behind
IAM-signed access during setup).
"""

import hmac
import os

from akd_ext.mcp.server import mcp

# Streamable-HTTP ASGI app, mounted at /mcp/ by default. Stateless = Lambda-safe.
_mcp_app = mcp.http_app(stateless_http=True)

_TOKEN = (os.getenv("MCP_AUTH_TOKEN") or "").strip()


class BearerAuthASGI:
    """Pure-ASGI bearer-token guard.

    Written at the ASGI layer (not BaseHTTPMiddleware) so it does not buffer the
    streaming MCP responses. Forwards non-HTTP scopes (e.g. lifespan) untouched so
    the wrapped app's startup/shutdown still runs.
    """

    def __init__(self, app, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            provided = headers.get(b"authorization", b"")
            if not hmac.compare_digest(provided, self.expected):
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                })
                await send({
                    "type": "http.response.body",
                    "body": b'{"error":"unauthorized"}',
                })
                return
        await self.app(scope, receive, send)


# Only enforce auth when a token is configured.
app = BearerAuthASGI(_mcp_app, _TOKEN) if _TOKEN else _mcp_app
