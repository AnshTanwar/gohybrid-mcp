# Contributing

Thanks for wanting to contribute! Here's how to get involved.

## Ways to contribute

- **Add a new fitness provider** — Whoop, Oura, Polar, Garmin Connect, Apple Health, etc. See guide below.
- **Improve an existing tool** — better descriptions, more useful return shapes, edge cases.
- **Fix a bug** — open an issue first if you're not sure it's a bug.
- **Improve docs** — README clarity, examples, deployment guides.
- **Report a security issue** — see `SECURITY.md` (do not file public issues for security).

## Before you start

For non-trivial changes, **open a GitHub issue first** to discuss the approach. Avoids wasted work if your design needs adjustment.

For small fixes (typos, obvious bugs, doc improvements) — just open a PR.

## Adding a new provider

The provider abstraction follows a consistent pattern. To add e.g. Whoop:

1. **Define the token shape** in your `_<provider>_creds()` helper:
   ```python
   def _whoop_creds() -> dict:
       c = get_creds()
       if c.get("p") != "whoop":
           raise RuntimeError("This tool requires a Whoop token.")
       return c
   ```

2. **Add a request helper** that uses session creds:
   ```python
   def _wget(path: str, params: dict | None = None) -> dict:
       token = _whoop_access_token()  # exchange refresh if needed
       r = httpx.get(f"{_WHOOP_BASE}{path}", params=params,
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
       r.raise_for_status()
       return r.json()
   ```

3. **Register tools** with read-only annotations:
   ```python
   @mcp.tool(annotations={"readOnlyHint": True})
   def get_whoop_recovery(days: int = 7) -> list[dict]:
       """Daily recovery score, HRV, RHR, strain."""
       return _wget("/cycle/recovery", {"days": days})
   ```

4. **Add the provider section to `connect.html`** and `oauth.py`'s authorize page — three tabs become four.

5. **Update the token format** in README and SECURITY.md.

6. **Write tests** in `tests/test_mcp_auth.py` for the new token shape.

Open the PR with the *minimum* surface area — one provider, the tools that provider actually supports. Don't over-build.

## Running tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

All tests must pass before merging.

## Local dev

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python server.py --http       # starts at http://localhost:8000
```

Endpoints to verify:
- `GET /health` → 200 OK
- `GET /connect` → token generator UI
- `GET /oauth/authorize?response_type=code&redirect_uri=http://localhost:9999/cb&code_challenge=abc&code_challenge_method=S256` → consent page
- `GET /.well-known/oauth-authorization-server` → metadata JSON

## Code style

- **No comments unless the *why* is non-obvious.** Well-named code documents itself; comments rot.
- **Type hints on all functions.**
- **Tools must be read-only** (`readOnlyHint: True`). This server never writes back to upstream APIs — that's a design constraint, not a default.
- **No new dependencies** without a strong reason. We're stdlib + `mcp` + `httpx` + `fastapi`. Adding a heavy dep needs justification.
- **Stateless everywhere.** No databases, no in-memory session state, no caches that outlive a single request. If you need to remember something across requests, encode it in a signed token.
- **Security-sensitive changes** (anything touching `oauth.py`, `auth.py`, token shape) get extra scrutiny. Open an issue first.

## PR checklist

- [ ] Tests pass (`pytest tests/ -v`)
- [ ] New code has type hints
- [ ] New tools have docstrings explaining what they return and when to use them
- [ ] If touching OAuth flow: re-tested PKCE end-to-end
- [ ] If adding a provider: README's "Tools reference" table updated
- [ ] No secrets committed (`.env` is gitignored; double-check)

## Questions?

Open a [GitHub Discussion](https://github.com/AnshTanwar/gohybrid-mcp/discussions) — they're great for design questions before you sink time into a PR.
