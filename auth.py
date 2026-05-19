import base64
import json
from contextvars import ContextVar
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_current_creds: ContextVar[dict | None] = ContextVar("_current_creds", default=None)


def encode_token(payload: dict[str, Any]) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
    return "ghi_" + raw.decode().rstrip("=")


def decode_token(token: str) -> dict[str, Any]:
    if not token.startswith("ghi_"):
        raise ValueError("Invalid token: must start with 'ghi_'. Generate one at /connect.")
    raw = token[4:]
    padding = (4 - len(raw) % 4) % 4
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * padding))
    except Exception as exc:
        raise ValueError(f"Invalid token: could not decode — {exc}") from exc
    return payload


def get_creds() -> dict[str, Any]:
    creds = _current_creds.get()
    if creds is None:
        raise RuntimeError(
            "No credentials found. Connect with header: "
            "Authorization: Bearer ghi_<your-token>  "
            "Generate your token at /connect."
        )
    return creds


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Reads `Authorization: Bearer ghi_<token>` and binds creds to the request
    context. On /mcp 401 responses, adds a `WWW-Authenticate` header pointing
    to the protected-resource metadata so MCP clients can auto-discover the
    auth server (RFC 9728 §5.1).
    """

    async def dispatch(self, request: Request, call_next):
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ghi_"):
            try:
                _current_creds.set(decode_token(auth[7:]))
            except ValueError:
                pass  # tools will surface the error when creds are accessed

        response = await call_next(request)

        if response.status_code == 401 and request.url.path.startswith("/mcp"):
            base = f"{request.url.scheme}://{request.headers.get('host', '')}"
            response.headers["WWW-Authenticate"] = (
                f'Bearer realm="gohybrid-mcp", '
                f'resource_metadata="{base}/.well-known/oauth-protected-resource"'
            )
        return response
