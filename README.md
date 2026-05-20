# allstar-mcp

MCP server for [AllStar Link](https://allstarlink.org) node control. Wraps [ASL3-API](https://github.com/KJ5IRQ/asl3-api) REST endpoints as MCP tools, giving AI agents (Claude, GPT-4, any MCP-compatible client) structured, intent-aware access to AllStar Link node monitoring and control.

The first MCP server for AllStar Link in existence.

## Architecture

```
Claude / AI Agent
      |
  allstar-mcp  (FastMCP, Python)
      |
  ASL3-API  (REST + SSE, port 8073)
      |
  AMI / Asterisk / app_rpt
```

The MCP server is a translation layer only. It never touches AMI directly — all operations go through ASL3-API REST endpoints.

## Requirements

- ASL3-API v1.4+ running on your AllStar node (see [ASL3-API](https://github.com/KJ5IRQ/asl3-api))
- Python 3.11+ or `uv`

## Quick Start

```bash
# Run directly with uvx (no install needed)
ALLSTAR_API_KEY=yourkey ALLSTAR_API_URL=http://your-node:8073 uvx allstar-mcp
```

## Claude Desktop Configuration

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "allstar": {
      "command": "uvx",
      "args": ["allstar-mcp"],
      "env": {
        "ALLSTAR_API_KEY": "your-api-key-here",
        "ALLSTAR_API_URL": "http://your-node-ip:8073"
      }
    }
  }
}
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ALLSTAR_API_KEY` | Yes | — | ASL3-API authentication key |
| `ALLSTAR_API_URL` | No | `http://localhost:8073` | Base URL of your ASL3-API instance |

## Tool Surface

### Read-only (no risk)

| Tool | Description |
|------|-------------|
| `get_node_status` | Uptime, keyup count, TX time, DTMF stats |
| `get_connected_nodes` | Who is linked and where (with callsign/location) |
| `get_live_variables` | Live RX/TX keyed state, link counts, autopatch status |
| `get_capabilities` | What this node supports — for agent auto-config |
| `lookup_node` | Callsign, location from 40k-node AllStar database |
| `get_audit_log` | Recent command history for agent context |

### Low-risk control

| Tool | Description |
|------|-------------|
| `cop_identify` | Play node ID over the air |
| `cop_time` | Say current time over the air |
| `cop_status` | Say system status over the air |
| `cop_version` | Say app_rpt version over the air |

### Medium-risk (require situational awareness)

| Tool | Description |
|------|-------------|
| `connect_node` | Connect to a remote node (check live_variables first) |
| `disconnect_node` | Disconnect from a specific node (confirm required) |
| `send_dtmf` | Send DTMF sequence (`confirmed=True` required) |
| `execute_macro` | Run a macro from rpt.conf (`confirmed=True` required) |

### High-risk (destructive)

| Tool | Description |
|------|-------------|
| `disconnect_all` | Drop ALL active connections (`confirmed=True` required) |

### Resources

| Resource | Description |
|----------|-------------|
| `allstar://events/stream` | SSE event stream URL and connection details |

## Safety Design

- **Live state check**: Tool descriptions instruct the agent to call `get_live_variables` before any connect/disconnect — avoids interrupting active QSOs
- **Confirmed flag**: `send_dtmf`, `execute_macro`, and `disconnect_all` require `confirmed=True` and will no-op and explain why if not set
- **Audit context**: `get_audit_log` lets the agent check recent command history before issuing duplicate commands
- **No AMI access**: The server has no Asterisk/AMI credentials and cannot bypass ASL3-API

## License

MIT
