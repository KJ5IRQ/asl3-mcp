# allstar-mcp

A small, client-neutral MCP interface to the ASL3 Remote Platform.

allstar-mcp does not control Asterisk or app_rpt directly. It translates nine
semantic MCP tools into the ASL3-API v1 REST contract. Authentication,
authorization, traffic policy, dispatch serialization, durable operations,
idempotency, verification, and radio safety remain authoritative in asl3-api.

No particular AI model, desktop client, or personal assistant is required.

## Architecture

    MCP client
       |
       | stdio
       v
    allstar-mcp
       |
       | authenticated HTTP
       v
    asl3-api
       |
       | AMI on localhost
       v
    Asterisk / app_rpt

The remote-network product is the REST API. The MCP server listens on stdio
only in v0.2.0.

## Requirements

- Python 3.10 or newer
- ASL3-API vNext/v1
- an ASL3-API credential with observe authority for read tools
- control authority for control tools

The MCP implementation uses the official Model Context Protocol Python SDK,
pinned to mcp==2.2.0.

## Quick start

Set the API endpoint and credential, then run the stdio server:

    export ALLSTAR_API_URL=http://127.0.0.1:8073
    export ALLSTAR_API_KEY='your-api-key'
    uvx allstar-mcp

ALLSTAR_API_URL defaults to http://127.0.0.1:8073.

For a remote node, connect to asl3-api through the protected transport
recommended by that project, such as Tailscale, WireGuard, or a TLS reverse
proxy. Do not expose Asterisk AMI for MCP clients.

Any MCP client that can launch a stdio server can use the same command and
environment variables. Client-specific configuration formats belong in the
client's own documentation.

## Tool surface

Exactly nine tools are exposed.

### Read-only

| Tool | Purpose |
|---|---|
| health_check | API reachability, auth, AMI health, and backend-contract compatibility |
| get_node_state | Fresh native node state with explicit unknown/incomplete evidence |
| lookup_node | Public directory metadata for a canonical AllStar node number |
| get_recent_operations | Recent durable control operations |
| get_operation_status | One durable operation by operation ID |

### Control

| Tool | Purpose |
|---|---|
| announce | Request identify, time, status, or version |
| link_node | Link one node in transceive or monitor mode |
| unlink_node | Remove one exact direct link, including a permanent link |
| unlink_all | Remove all direct links, including permanent links |

There are no generic REST, AMI, COP, shell, DTMF, macro, force, emergency,
confirmation, dry-run, QSO-bypass, or arbitrary-command tools.

There are also no MCP resources, prompts, Tasks, or remote MCP listener in this
release.

## Result model

Control tools do not return a simplistic success=true.

ASL3-API creates a durable operation and tracks separate dispatch and observed
effect states. MCP returns that operation unchanged inside a small envelope
containing the idempotency key and operation document.

Call get_operation_status to learn what happened later.

STATE_UNKNOWN or UNKNOWN evidence is never converted into false, and
OUTCOME_UNKNOWN is never converted into success or failure.

## Retry behavior

Each control tool invocation:

1. checks the backend capability contract,
2. generates one Idempotency-Key,
3. makes at most one control HTTP request.

The MCP adapter has no automatic control retry loop and configures the HTTP
transport with retries disabled.

If the control response is lost, the tool returns
CONTROL_RESPONSE_UNCERTAIN, the key used for that logical attempt, and
retried=false. It does not issue a second radio operation.

A received ASL3-API problem response is preserved with its stable code and
detail. API credentials are redacted from surfaced errors.

## Compatibility gate

Before any control request, the adapter checks /v1/capabilities for the
contract it depends on:

- API contract 1
- result contract 1.0
- native app_rpt AMI backend contract
- durable operations
- idempotency support
- single control ownership
- automatic control replay disabled
- required semantic operations

An incompatible backend blocks MCP control. Read-only tools remain available
when they can be used safely.

This is compatibility checking, not radio policy. Traffic eligibility and other
operator policy remain protected inside asl3-api and cannot be weakened by MCP
arguments.

## Node identifiers

Controllable AllStar node targets must match:

    ^[1-9][0-9]{0,5}$

That means 1 to 6 ASCII decimal digits, with no leading zero, whitespace,
Unicode digits, signs, shorthand, or sentinel values.

Directory lookup is informational. A private/static node can still be a valid
target even if it is absent from the public directory.

## Development

    python3 -m venv .venv
    .venv/bin/pip install -e '.[dev]'
    .venv/bin/ruff check src/ tests/
    .venv/bin/pytest -q

The tests use httpx.MockTransport and the official MCP SDK. They do not access
a live AllStar node or issue radio commands.

CI runs on Python 3.10 and 3.13, lints, tests, and builds the package.

## Security boundary

allstar-mcp is intentionally not the safety boundary.

It has no AMI credentials and cannot:

- weaken protected traffic policy,
- increase its API credential authority,
- request a QSO override,
- replay an uncertain control operation,
- invoke raw app_rpt functions,
- bypass the durable operation ledger.

MCP tool annotations are client hints only. ASL3-API remains authoritative.

## License

MIT
