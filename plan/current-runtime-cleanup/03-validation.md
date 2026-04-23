# Validation Checklist

## HTTP surfaces

- `/health` returns OK
- `/search` returns Perplexity-style concise JSON
- `/research` accepts the relay-style request shape and streams Vane output
- `/compat/searxng/search` returns SearxNG-shaped JSON
- `/fetch` still works
- `/extract` still works

## MCP parity

- MCP `search` mirrors `/search`
- MCP `research` mirrors `/research`
- MCP fetch/extract/ops tools still work

## Runtime expectations

- no compiler/planner/local research synthesis on the `/research` path
- no response remapping on the `/research` path
- provider rotation/cooldown remains intact for local search
- config/docs reflect only active runtime knobs
