# Plan Directory

This directory only tracks the current cleanup/refactor plan for the live enhanced-websearch runtime.

The active plan is under `plan/current-runtime-cleanup/`.

Older architecture and milestone plans were removed after the service was simplified to:
- `/search` as concise local search
- `/research` as a transparent Vane relay
- `/compat/searxng` as a compatibility adapter
- `/fetch` and `/extract` as local fetch/extract helpers
- MCP mirroring the HTTP surfaces
