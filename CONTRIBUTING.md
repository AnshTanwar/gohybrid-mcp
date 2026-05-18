# Contributing

## Adding a new provider

1. Add `_<provider>_creds()` and `_<provider>get()` helpers following the intervals.icu pattern in `server.py`
2. Register tools with `@mcp.tool(annotations={"readOnlyHint": True})`
3. Add the provider section to `connect.html` (token generation form)
4. Update the token format docs in README

## Running tests

```bash
pip install -r requirements.txt
pytest tests/test_mcp_auth.py -v
```

## Local dev

```bash
python server.py --http   # starts at http://localhost:8000
```

## Code style

- No comments unless the why is non-obvious
- Type hints on all functions
- Tools must be read-only (`readOnlyHint: True`) — this server never writes
