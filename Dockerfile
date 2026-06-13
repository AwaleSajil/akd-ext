# Container image for running the akd-ext MCP server on AWS Lambda.
#
# Uses the AWS Lambda Web Adapter (LWA): a Lambda extension that bridges the
# Lambda Function URL to a normal HTTP server (uvicorn) running inside the image.
# This lets the FastMCP ASGI app run unchanged — no Lambda handler rewrite.

FROM python:3.12-slim

# Copy the Lambda Web Adapter binary into the image as a Lambda extension.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.0 /lambda-adapter /opt/extensions/lambda-adapter

# LWA config: our server listens on 8080; stream responses back to the caller.
ENV PORT=8080 \
    AWS_LWA_INVOKE_MODE=response_stream \
    PYTHONUNBUFFERED=1

# Build tools needed by some wheels (pymupdf, etc.), removed after install.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the package (and its git dependency akd-core, which is public).
COPY . .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

# Start the MCP server. uvicorn serves the stateless streamable-http ASGI app.
CMD ["uvicorn", "lambda_app:app", "--host", "0.0.0.0", "--port", "8080"]
