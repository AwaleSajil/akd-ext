"""ASGI entrypoint for running the akd-ext MCP server on AWS Lambda.

The AWS Lambda Web Adapter runs this as a normal HTTP app (via uvicorn), so no
Lambda-specific handler is needed. ``stateless_http=True`` makes every MCP request
independent (no long-lived SSE session), which is what Lambda's request/response
model requires.

AWS credentials are taken from the Lambda execution role automatically (boto3's
default chain), so NO AWS_* key env vars should be set on the function.

Access control: if the env var ``MCP_AUTH_TOKEN`` is set, every HTTP request must
present that token, either as ``Authorization: Bearer <token>`` or as the custom
header ``X-MCP-Token: <token>``; otherwise it is rejected with 401. Two headers are
accepted on purpose:

  - ``Authorization: Bearer`` is the standard MCP-client form, used once the
    Function URL is public.
  - ``X-MCP-Token`` avoids the ``Authorization`` header, so the token can coexist
    with AWS IAM (SigV4) request signing, which also uses ``Authorization``.

If the var is unset, no check is applied.
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
        self.expected_auth = f"Bearer {token}".encode()
        self.expected_token = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            authorized = hmac.compare_digest(
                headers.get(b"authorization", b""), self.expected_auth
            ) or hmac.compare_digest(
                headers.get(b"x-mcp-token", b""), self.expected_token
            )
            if not authorized:
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
