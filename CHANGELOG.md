# Changelog

All notable changes to allstar-mcp are documented here.

## [0.2.0] - 2026-09-19

### Changed

- Rebuilt the server as a client-neutral semantic adapter for ASL3 Remote
  Platform v1.
- Replaced FastMCP with the official MCP Python SDK, pinned to mcp==2.2.0.
- Reduced the public surface to exactly nine tools.
- Switched all control paths to ASL3-API v1 durable operation resources.
- Added backend capability checks before control.
- Added one-key/one-request control semantics with no automatic retry after an
  uncertain response.
- Added typed structured results and MCP tool annotations.
- Lowered the supported Python floor to 3.10 and added Python 3.10/3.13 CI.

### Removed

- Client-side active-QSO policy and fail-open state checks.
- confirmed, dry_run, override_active_qso, and similar caller overrides.
- DTMF, macro, raw COP, and generic command tools.
- The MCP SSE resource and all other MCP resources/prompts.
- Per-call HTTP clients and legacy ASL3-API endpoint mirroring.

### Safety

- ASL3-API is the sole authority for authentication, authorization, protected
  traffic policy, serialization, dispatch, verification, idempotency, and
  operation history.
- Control transport errors surface CONTROL_RESPONSE_UNCERTAIN and are never
  automatically retried by the MCP adapter.
- Canonical control targets are restricted to 1-6 ASCII digits with no leading
  zero.

---

## [0.1.2] - 2026-05-19

- Added CI and changelog.
- Added dry-run documentation and PyPI packaging.

## [0.1.1] - 2026-05-19

- Added the original active-QSO guard, confirmation flags, dry-run behavior,
  and test coverage. These client-side safety mechanisms were retired in 0.2.0
  in favor of authoritative server-side policy.

## [0.1.0] - 2026-05-19

- Initial FastMCP REST-wrapper release.
