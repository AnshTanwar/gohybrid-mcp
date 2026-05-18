"""
gohybrid-mcp — fitness data MCP server + Claude connector

Use as a hosted connector:
    python server.py --http

Embed in your FastAPI app:
    from gohybrid_mcp import create_mcp_app, AuthMiddleware
    app.add_middleware(AuthMiddleware)
    app.mount("/fitness", create_mcp_app())

Generate tokens programmatically:
    from gohybrid_mcp import encode_token
    token = encode_token({"p": "intervals", "id": "i123", "k": "your-api-key"})
"""

from .auth import (
    encode_token,
    decode_token,
    get_creds,
    AuthMiddleware,
    _current_creds,
)
from .server import mcp, create_mcp_app, create_server_app

__version__ = "0.1.0"
__all__ = [
    "encode_token",
    "decode_token",
    "get_creds",
    "AuthMiddleware",
    "mcp",
    "create_mcp_app",
    "create_server_app",
]
