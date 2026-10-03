"""MCP-compatible error rendering and the tool error boundary.

Every tool body is wrapped by :func:`tool_errors`, which is the single place
where exceptions become MCP results. Consequences:

* Raw exceptions never cross into the MCP layer; the client always receives a
  structured canonical error object.
* Credential values are scrubbed from every rendered message, as a second line
  of defence behind :mod:`warera_mcp.auth.credentials`.
* Unexpected failures are logged by exception *type* only — never a message that
  might embed request data.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from mcp.types import CallToolResult, TextContent

from warera_mcp import errors as app_errors
from warera_mcp.application.common import map_credential_error
from warera_mcp.auth.credentials import CredentialError
from warera_mcp.auth.redaction import SecretRedactor
from warera_mcp.observability.logging import make_logger

F = TypeVar("F", bound=Callable[..., Awaitable[CallToolResult]])

#: Argument names that carry credential material.
_SECRET_ARGUMENT_FIELDS = ("api_key", "jwt")


def redactor_from_arguments(arguments: Mapping[str, Any]) -> SecretRedactor:
    """Build a redactor from the raw ``player_context`` argument, if present."""
    payload = arguments.get("player_context")
    if not isinstance(payload, Mapping):
        return SecretRedactor()
    return SecretRedactor(
        [
            value
            for key, value in payload.items()
            if key in _SECRET_ARGUMENT_FIELDS and isinstance(value, str)
        ]
    )


def correlation_from_arguments(arguments: Mapping[str, Any]) -> str | None:
    context = arguments.get("ctx")
    request_id = getattr(context, "request_id", None)
    return None if request_id is None else str(request_id)


def render_error(
    error: app_errors.AppError, *, redactor: SecretRedactor | None = None
) -> CallToolResult:
    """Render a canonical error as an MCP error result."""
    payload = error.to_payload(redactor=redactor)
    body = payload["error"]
    lines = [f"{body['code']}: {body['message']}"]
    if body.get("user_message"):
        lines.append(str(body["user_message"]))
    if body.get("action") and body["action"] != app_errors.ErrorAction.NONE.value:
        lines.append(f"Suggested action: {body['action']}")
    text = "\n".join(lines)
    if redactor is not None:
        text = redactor.scrub(text)
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=payload,
        isError=True,
    )


def _log_internal_error(
    arguments: Mapping[str, Any],
    operation: str,
    correlation_id: str | None,
    exception: Exception,
) -> None:
    """Log an unexpected failure without message bodies."""
    make_logger("warera_mcp.mcp_server").error(
        "tool_internal_error",
        tool=operation,
        correlation_id=correlation_id,
        status=type(exception).__name__,
    )


def tool_errors(operation: str) -> Callable[[F], F]:
    """Wrap a tool body so every failure becomes a structured MCP error result."""

    def decorate(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> CallToolResult:
            redactor = redactor_from_arguments(kwargs)
            correlation_id = correlation_from_arguments(kwargs)
            try:
                return await fn(*args, **kwargs)
            except app_errors.AppError as error:
                return render_error(error, redactor=redactor)
            except CredentialError as error:
                return render_error(map_credential_error(error, operation), redactor=redactor)
            except Exception as exception:
                _log_internal_error(kwargs, operation, correlation_id, exception)
                return render_error(
                    app_errors.internal_error(
                        "the server failed to complete this request",
                        operation,
                        correlation_id=correlation_id,
                    ),
                    redactor=redactor,
                )

        return wrapper  # type: ignore[return-value]

    return decorate


__all__ = [
    "correlation_from_arguments",
    "redactor_from_arguments",
    "render_error",
    "tool_errors",
]
