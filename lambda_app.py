"""ASGI entrypoint for running the akd-ext MCP server on AWS Lambda.

The AWS Lambda Web Adapter runs this as a normal HTTP app (via uvicorn), so no
Lambda-specific handler is needed. ``stateless_http=True`` makes every MCP request
independent (no long-lived SSE session), which is what Lambda's request/response
model requires.

AWS credentials are taken from the Lambda execution role automatically (boto3's
default chain), so NO AWS_* key env vars should be set on the function.
"""

from akd_ext.mcp.server import mcp

# Streamable-HTTP ASGI app, mounted at /mcp/ by default. Stateless = Lambda-safe.
app = mcp.http_app(stateless_http=True)
