# Open WebUI Enhancements

A collection of custom Open-WebUI tools and pipes for enhanced retrieval, research, and workflow capabilities.

## Repository Layout

- enhanced-websearch/
  - enhanced_websearch.py: Tool version (callable search tool)
  - enhanced_websearch_pipe.py: Pipe/function version (model-driven workflow)
  - README.md: Module-specific setup and valve documentation

## Current Module

### enhanced-websearch

Enhanced web retrieval and research for Open-WebUI with:

- Query expansion + Reciprocal Rank Fusion (RRF)
- Concurrent scraping with FlareSolverr fallback
- Optional Vane deep synthesis
- Research mode with iterative follow-up planning
- Mode overrides via query prefix: `fast:` and `deep:`

See module docs: `enhanced-websearch/README.md`

## Usage in Open-WebUI

1. Open Admin Panel in Open-WebUI.
2. Import the script you need from `enhanced-websearch/`.
3. Configure admin valves (SearXNG required; Vane optional unless deep mode is used).
4. Optionally configure user valves for mode/status/citations.

## Adding New Enhancements

Create a new top-level folder per enhancement and include:

- Tool and/or pipe script(s)
- Module README with valves, defaults, and mandatory settings
- Minimal usage examples

Suggested naming:

- Folder: `kebab-case`
- Python files: `snake_case`

## Development Notes

- Keep scripts standalone and import-ready for Open-WebUI.
- Avoid committing generated artifacts (`__pycache__`, `.pyc`).
- Prefer configurable valves over hardcoded endpoints.
