# Security

## Reporting a vulnerability

If you discover a security issue, please **do not open a public GitHub issue**. Email the maintainer directly at the address in `pyproject.toml`, or open a private security advisory on GitHub. We'll respond within 72 hours.

## Threat model

GoHybrid is a stateless OAuth wrapper around fitness-data APIs (intervals.icu, Strava). It stores **nothing** server-side — no user accounts, no databases, no session state. All credentials live inside the `ghi_` Bearer token itself.

### What we protect against

| Threat | Mitigation |
|---|---|
| Auth code interception (network) | PKCE S256 **mandatory** for all OAuth flows |
| Code replay | Auth codes are single-use, expire after 5 min |
| Open-redirect phishing | `redirect_uri` must be HTTPS (or `localhost` for dev) |
| Token forgery | Auth codes are HMAC-SHA256 signed with a per-process secret |
| Discovery downgrade | `WWW-Authenticate` header on 401 advertises the auth server (RFC 9728) |

### What we do **not** protect against

| Risk | Why | What to do |
|---|---|---|
| Token leakage | `ghi_` tokens embed the user's Strava/intervals.icu credentials (base64-encoded, not encrypted). Anyone with the token has full read access. | Treat tokens like passwords. Don't paste them in chat, logs, or screenshots. Regenerate if exposed. |
| Hosting compromise | A malicious host could log the OAuth submission and steal credentials. | Only use a deployment you control, or trust the host operator. |
| Phishing | The OAuth flow shows a credential form on a `https://your-deployment` URL. A look-alike domain can phish. | Verify the URL before entering credentials. |

### Token format (intentional design)

```
ghi_<base64url(JSON)>
JSON = {"p": "intervals", "id": "i123", "k": "api-key"}
       or
       {"p": "strava", "cid": "12345", "cs": "secret", "rt": "refresh-token"}
```

The token is **not encrypted** — only base64url-encoded. This is by design: the server is stateless and needs to recover the original credentials on every request. The base64 layer is just transport encoding, not confidentiality. Treat the entire token as a long-lived API key with full read access.

If this trade-off is unacceptable for your use case, run a **self-hosted deployment** so only you ever see the tokens.

## Hardening for production deployments

If you're hosting this for others:

1. **Set `OAUTH_SIGNING_SECRET`** as an environment variable (32+ random bytes). Otherwise it regenerates on every restart, invalidating in-flight auth codes.
2. **Rate-limit `/oauth/register`** — DCR is unauthenticated. Use Cloudflare, Fastly, or a reverse-proxy rate limiter.
3. **Rate-limit `/oauth/authorize` POST** — same reason, prevents brute-force enumeration of valid Strava refresh tokens.
4. **Enable HTTPS termination at the proxy** — server trusts `X-Forwarded-Proto` / `X-Forwarded-Host`.
5. **Log redactor** — never log the raw `Authorization` header or full request bodies on `/oauth/authorize` (POST contains client secrets in the Strava case).

## Cryptographic choices

- **Auth code signing:** HMAC-SHA256 with a 32-byte random key. Reasonable for 5-minute-lifetime codes; not designed to resist offline brute force over years.
- **PKCE:** S256 only (plain explicitly rejected even though spec allows it).
- **Token format:** Base64url-encoded JSON. No encryption layer — see "Token format" above.

## Known limitations

- No refresh tokens — access tokens never expire. By design (Strava's refresh token is embedded). Trade-off: simpler stateless server, but a leaked token grants permanent access until you revoke at the upstream provider.
- No token revocation endpoint — to revoke, regenerate your Strava refresh token at strava.com/settings/api.
- No multi-instance signing-secret coordination — auth codes issued by one instance can't be verified by another unless `OAUTH_SIGNING_SECRET` is shared.
