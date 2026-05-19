"""
OAuth 2.1 authorization server for the GoHybrid MCP connector.

Lets Claude.ai (web) connect via the standard OAuth flow instead of pasting
raw Bearer tokens. Fully stateless — auth codes are signed JWT-like blobs
that embed the user's ghi_ token. PKCE is enforced.

Endpoints exposed:
  /.well-known/oauth-authorization-server  → discovery metadata (RFC 8414)
  /.well-known/oauth-protected-resource    → protected resource metadata (RFC 9728)
  /register                                → Dynamic Client Registration (RFC 7591)
  /authorize                               → user-facing consent page
  /token                                   → exchange auth code for access token
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

try:
    from .auth import decode_token
except ImportError:
    from auth import decode_token


# Signing secret for auth codes. Generated once per process. A restart
# invalidates outstanding auth codes (60s window), which is acceptable.
_SIGNING_SECRET = os.environ.get("OAUTH_SIGNING_SECRET") or secrets.token_urlsafe(32)
_CODE_TTL_SECONDS = 300  # 5 min — auth codes are exchanged within seconds normally


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = (4 - len(s) % 4) % 4
    return base64.urlsafe_b64decode(s + "=" * padding)


def _sign_code(payload: dict[str, Any]) -> str:
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(_SIGNING_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def _verify_code(code: str) -> dict[str, Any]:
    try:
        body, sig = code.split(".", 1)
        sig_bytes = _b64url_decode(sig)
        expected = hmac.new(_SIGNING_SECRET.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(sig_bytes, expected):
            raise HTTPException(400, "invalid_grant: bad signature")
        payload = json.loads(_b64url_decode(body))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "invalid_grant: malformed code")
    if payload.get("exp", 0) < time.time():
        raise HTTPException(400, "invalid_grant: code expired")
    return payload


def _pkce_matches(verifier: str, challenge: str, method: str) -> bool:
    if method == "plain":
        return secrets.compare_digest(verifier, challenge)
    if method == "S256":
        digest = hashlib.sha256(verifier.encode()).digest()
        return secrets.compare_digest(_b64url_encode(digest), challenge)
    return False


def _server_base_url(request: Request) -> str:
    # Honor X-Forwarded-* when behind Render/Cloudflare.
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return f"{scheme}://{host}".rstrip("/")


router = APIRouter()


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(request: Request) -> JSONResponse:
    base = _server_base_url(request)
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["fitness:read"],
    })


@router.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata(request: Request) -> JSONResponse:
    base = _server_base_url(request)
    return JSONResponse({
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "scopes_supported": ["fitness:read"],
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{base}/connect",
    })


@router.post("/oauth/register")
async def register_client(request: Request) -> JSONResponse:
    """Dynamic Client Registration — accept anything, return a client_id.

    We don't actually track clients server-side. The auth code itself encodes
    the redirect_uri so we can verify it at /token time.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    client_id = "mcp_" + secrets.token_urlsafe(16)
    return JSONResponse(
        status_code=201,
        content={
            "client_id": client_id,
            "client_name": body.get("client_name", "MCP Client"),
            "client_id_issued_at": int(time.time()),
            "redirect_uris": body.get("redirect_uris", []),
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "scope": "fitness:read",
            "token_endpoint_auth_method": "none",
        },
    )


_AUTHORIZE_PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sign in to GoHybrid — Claude MCP</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;background:#fafafa;color:#1a1a1a;min-height:100vh;padding:64px 20px;font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
.container{max-width:420px;margin:0 auto}
h1{font-size:1.25rem;font-weight:600;margin-bottom:4px;letter-spacing:-0.01em}
.subtitle{color:#6b7280;margin-bottom:28px;font-size:.875rem;line-height:1.55}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:24px}
.consent{font-size:.8125rem;color:#4b5563;line-height:1.6;padding:12px 14px;border-radius:6px;margin-bottom:20px;background:#f9fafb;border:1px solid #e5e7eb}
.consent b{color:#111827;font-weight:500}
.tab-row{display:flex;gap:0;margin-bottom:20px;border-bottom:1px solid #e5e7eb}
.tab{padding:8px 12px;cursor:pointer;color:#6b7280;border-bottom:2px solid transparent;font-size:.8125rem;font-weight:500;margin-bottom:-1px;transition:color .12s,border-color .12s}
.tab:hover{color:#1f2937}
.tab.active{color:#111827;border-bottom-color:#111827}
.pane{display:none}
.pane.active{display:block}
label{display:block;font-size:.75rem;color:#4b5563;margin-bottom:5px;margin-top:14px;font-weight:500}
.pane > *:first-child{margin-top:0}
input{width:100%;padding:8px 11px;background:#fff;border:1px solid #d1d5db;border-radius:6px;color:#111827;font-size:.8125rem;outline:none;transition:border-color .12s,box-shadow .12s;font-family:inherit}
input:focus{border-color:#111827;box-shadow:0 0 0 3px rgba(17,24,39,.08)}
input::placeholder{color:#9ca3af}
.hint{font-size:.75rem;color:#6b7280;margin-top:8px;line-height:1.5}
.btn{margin-top:20px;width:100%;padding:9px 14px;background:#111827;color:#fff;border:none;border-radius:6px;font-size:.8125rem;font-weight:500;cursor:pointer;transition:background .12s;font-family:inherit;letter-spacing:.01em}
.btn:hover{background:#000}
a{color:#4b5563;text-decoration:underline;text-decoration-color:#d1d5db;text-underline-offset:2px}
a:hover{color:#111827;text-decoration-color:#9ca3af}
.error{background:#fef2f2;color:#991b1b;border:1px solid #fecaca;padding:9px 12px;border-radius:6px;font-size:.8125rem;margin-bottom:16px;display:%ERROR_DISPLAY%}
.footer{text-align:center;margin-top:20px;font-size:.75rem;color:#9ca3af}
</style></head><body>
<div class="container">
<h1>Sign in to GoHybrid</h1>
<p class="subtitle">Claude wants to connect to your fitness data to read training activities, wellness, and analytics.</p>

<div class="error">%ERROR_MESSAGE%</div>

<div class="card">
<div class="consent">
<b>Claude</b> will get read-only access to your activities, wellness data, and analytics from intervals.icu or Strava. Claude cannot modify or delete anything.
</div>

<div class="tab-row">
<div class="tab active" data-tab="intervals">intervals.icu</div>
<div class="tab" data-tab="strava">Strava</div>
<div class="tab" data-tab="existing">Existing token</div>
</div>

<form method="POST" action="/oauth/authorize" id="form">
<input type="hidden" name="oauth_state" value="%OAUTH_STATE%">

<div class="pane active" data-pane="intervals">
<label>Athlete ID</label>
<input name="iv_id" placeholder="i523248" autocomplete="off">
<label>API Key</label>
<input name="iv_key" type="password" placeholder="Your intervals.icu API key" autocomplete="off">
<p class="hint">Find both at <a href="https://intervals.icu/settings" target="_blank">intervals.icu/settings</a> → Developer.</p>
</div>

<div class="pane" data-pane="strava">
<label>Client ID</label>
<input name="st_cid" placeholder="12345" autocomplete="off">
<label>Client Secret</label>
<input name="st_cs" type="password" placeholder="Your client secret" autocomplete="off">
<label>Refresh Token</label>
<input name="st_rt" type="password" placeholder="Your refresh token" autocomplete="off">
<p class="hint">Get all three at <a href="https://www.strava.com/settings/api" target="_blank">strava.com/settings/api</a>.</p>
</div>

<div class="pane" data-pane="existing">
<label>Token</label>
<input name="existing_token" type="password" placeholder="ghi_..." autocomplete="off">
<p class="hint">If you've already generated a token at <a href="/connect">/connect</a>, paste it here.</p>
</div>

<input type="hidden" name="provider" value="intervals" id="provider">
<button class="btn" type="submit">Authorize Claude</button>
</form>
</div>
<p class="footer">You'll be redirected back to Claude after authorizing.</p>
</div>

<script>
document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.pane').forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  document.querySelector(`.pane[data-pane="${t.dataset.tab}"]`).classList.add('active');
  document.getElementById('provider').value = t.dataset.tab;
});
</script>
</body></html>
"""


def _render_authorize(oauth_state: str, error: str = "") -> str:
    return (
        _AUTHORIZE_PAGE
        .replace("%OAUTH_STATE%", oauth_state)
        .replace("%ERROR_DISPLAY%", "block" if error else "none")
        .replace("%ERROR_MESSAGE%", error)
    )


def _validate_redirect_uri(uri: str) -> None:
    """Reject non-HTTPS redirect_uris (with localhost exception for dev)."""
    from urllib.parse import urlparse
    if not uri:
        raise HTTPException(400, "redirect_uri is required")
    parsed = urlparse(uri)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1"):
        return
    raise HTTPException(400, "redirect_uri must use HTTPS (http://localhost permitted for development)")


@router.get("/oauth/authorize")
async def authorize_get(
    request: Request,
    response_type: str = "code",
    client_id: str = "",
    redirect_uri: str = "",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    scope: str = "",
) -> HTMLResponse:
    if response_type != "code":
        raise HTTPException(400, "only response_type=code is supported")
    _validate_redirect_uri(redirect_uri)
    if not code_challenge:
        raise HTTPException(400, "code_challenge is required (PKCE mandatory)")
    if code_challenge_method != "S256":
        raise HTTPException(400, "only code_challenge_method=S256 is supported")

    oauth_state = _b64url_encode(json.dumps({
        "redirect_uri": redirect_uri,
        "state": state,
        "client_id": client_id,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
    }, separators=(",", ":")).encode())
    return HTMLResponse(_render_authorize(oauth_state))


@router.post("/oauth/authorize")
async def authorize_post(
    request: Request,
    oauth_state: str = Form(...),
    provider: str = Form("intervals"),
    iv_id: str = Form(""),
    iv_key: str = Form(""),
    st_cid: str = Form(""),
    st_cs: str = Form(""),
    st_rt: str = Form(""),
    existing_token: str = Form(""),
) -> RedirectResponse:
    try:
        oauth_params = json.loads(_b64url_decode(oauth_state))
    except Exception:
        raise HTTPException(400, "invalid oauth_state")

    try:
        from .auth import encode_token
    except ImportError:
        from auth import encode_token

    if provider == "existing":
        if not existing_token.startswith("ghi_"):
            return HTMLResponse(_render_authorize(oauth_state, "Token must start with ghi_"), status_code=400)
        try:
            decode_token(existing_token)
        except ValueError as e:
            return HTMLResponse(_render_authorize(oauth_state, f"Invalid token: {e}"), status_code=400)
        ghi_token = existing_token
    elif provider == "intervals":
        if not iv_id or not iv_key:
            return HTMLResponse(_render_authorize(oauth_state, "Athlete ID and API Key are required"), status_code=400)
        ghi_token = encode_token({"p": "intervals", "id": iv_id.strip(), "k": iv_key.strip()})
    elif provider == "strava":
        if not st_cid or not st_cs or not st_rt:
            return HTMLResponse(_render_authorize(oauth_state, "Client ID, Client Secret, and Refresh Token are all required"), status_code=400)
        ghi_token = encode_token({"p": "strava", "cid": st_cid.strip(), "cs": st_cs.strip(), "rt": st_rt.strip()})
    else:
        raise HTTPException(400, "unknown provider")

    # Issue the auth code
    code = _sign_code({
        "token": ghi_token,
        "redirect_uri": oauth_params["redirect_uri"],
        "code_challenge": oauth_params.get("code_challenge", ""),
        "code_challenge_method": oauth_params.get("code_challenge_method", "plain"),
        "client_id": oauth_params.get("client_id", ""),
        "exp": int(time.time()) + _CODE_TTL_SECONDS,
    })

    qs = urlencode({"code": code, "state": oauth_params.get("state", "")})
    redirect = oauth_params["redirect_uri"]
    sep = "&" if "?" in redirect else "?"
    return RedirectResponse(f"{redirect}{sep}{qs}", status_code=302)


@router.post("/oauth/token")
async def token_endpoint(
    grant_type: str = Form(...),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    client_id: str = Form(""),
    code_verifier: str = Form(""),
) -> JSONResponse:
    if grant_type != "authorization_code":
        raise HTTPException(400, "unsupported_grant_type")
    if not code:
        raise HTTPException(400, "code is required")

    payload = _verify_code(code)

    if redirect_uri and redirect_uri != payload.get("redirect_uri"):
        raise HTTPException(400, "redirect_uri mismatch")

    challenge = payload.get("code_challenge", "")
    method = payload.get("code_challenge_method", "S256")
    if not challenge:
        raise HTTPException(400, "invalid_grant: code was issued without PKCE")
    if not code_verifier or not _pkce_matches(code_verifier, challenge, method):
        raise HTTPException(400, "invalid_grant: PKCE verification failed")

    return JSONResponse({
        "access_token": payload["token"],
        "token_type": "Bearer",
        "scope": "fitness:read",
    })
