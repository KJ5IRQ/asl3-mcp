# Changelog

All notable changes to allstar-mcp are documented here.

## [0.1.2] — 2026-05-19

### Added
- GitHub Actions CI: runs `ruff check`, `pytest -q`, and `uv build` on every push and pull request
- CHANGELOG.md
- Dry-run mode listed in README Safety Design section

### Fixed
- README Safety Design: replaced the stale "instruct the agent to call get_live_variables" line with an accurate description of the automatic active-QSO guard introduced in v0.1.1
- README Quick Start: now uses plain `uvx allstar-mcp` after first PyPI publish

### Published
- First release on PyPI (`pip install allstar-mcp` / `uvx allstar-mcp`)

---

## [0.1.1] — 2026-05-19

### Added
- `health_check` tool: returns structured JSON covering `api_reachable`, `auth_ok`, `ami_connected`, `node`, `callsign`, `api_version`, and `error`
- `dry_run=True` parameter on all five confirmed-action tools (`connect_node`, `disconnect_node`, `send_dtmf`, `execute_macro`, `disconnect_all`): returns a preview dict (`action`, `would_send`, `note`) without hitting the API or requiring `confirmed=True`
- Automatic active-QSO guard in `connect_node` and `disconnect_node`: calls `/variables` internally and blocks if `txkeyed` or `rxkeyed` is true; bypassed with `override_active_qso=True`
- `confirmed=False` guard on `connect_node` and `disconnect_node` (previously only on `send_dtmf`, `execute_macro`, `disconnect_all`)
- `_validate_macro_number()`: digits-only validation on `execute_macro`, placed before the `confirmed` check so bad input is rejected immediately
- Input validation: node numbers must be digits-only; DTMF sequences must contain only `0-9`, `*`, `#`
- 47-test pytest suite covering all safety guards, input validation, dry-run previews, QSO blocking, and `health_check` failure modes
- `pytest` added to `[project.optional-dependencies] dev`
- MIT LICENSE file

### Fixed
- **Security**: `events_stream_info()` no longer returns the real API key — always renders `<ALLSTAR_API_KEY>` placeholder
- README Quick Start: replaced non-functional `uvx allstar-mcp` with `uvx --from git+https://github.com/KJ5IRQ/asl3-mcp allstar-mcp` (pre-PyPI form)
- Removed unused imports (`Annotated`, `fastmcp.resources.Resource`) flagged by ruff

---

## [0.1.0] — 2026-05-19

### Added
- Initial release — the first MCP server for AllStar Link in existence
- 15 MCP tools wrapping all ASL3-API v1.4 REST endpoints:
  - Read-only: `get_node_status`, `get_connected_nodes`, `get_live_variables`, `get_capabilities`, `lookup_node`, `get_audit_log`
  - Low-risk control: `cop_identify`, `cop_time`, `cop_status`, `cop_version`
  - Confirmed-action: `connect_node`, `disconnect_node`, `send_dtmf`, `execute_macro`, `disconnect_all`
- `allstar://events/stream` MCP resource with SSE connection details
- `confirmed=False` guard on `send_dtmf`, `execute_macro`, `disconnect_all`
- Agent-intent tool descriptions with safety instructions embedded in docstrings
- Server-level instructions guiding agents to check state before acting
- FastMCP-based server, invocable via `uvx allstar-mcp`
- `ALLSTAR_API_KEY` and `ALLSTAR_API_URL` environment variable configuration
- `pyproject.toml` with hatchling build, console script entry point, PyPI metadata
