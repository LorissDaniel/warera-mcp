FROM ghcr.io/astral-sh/uv:0.12.22 AS uv
FROM python:3.12-slim-bookworm AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim-bookworm
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WARERA_MCP_HOST=0.0.0.0 \
    WARERA_MCP_STATELESS_HTTP=true \
    WARERA_MCP_REQUIRE_CLIENT_AUTH=false
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY deploy/entrypoint.sh /app/entrypoint.sh
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["/bin/sh", "/app/entrypoint.sh"]
