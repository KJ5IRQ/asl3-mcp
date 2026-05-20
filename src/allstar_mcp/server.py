"""AllStar Link MCP server — wraps ASL3-API REST endpoints as MCP tools."""

from __future__ import annotations

import os
from typing import Annotated

import httpx
from fastmcp import FastMCP
from fastmcp.resources import Resource

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _base_url() -> str:
    url = os.environ.get("ALLSTAR_API_URL", "http://localhost:8073")
    return url.rstrip("/")

def _api_key() -> str:
    key = os.environ.get("ALLSTAR_API_KEY", "")
    if not key:
        raise RuntimeError(
            "ALLSTAR_API_KEY environment variable is not set. "
            "Set it to your ASL3-API key before starting the MCP server."
        )
    return key

def _headers() -> dict[str, str]:
    return {"x-api-key": _api_key()}

def _get(path: str, **params: object) -> object:
    filtered = {k: v for k, v in params.items() if v is not None}
    with httpx.Client() as client:
        r = client.get(
            f"{_base_url()}{path}",
            headers=_headers(),
            params=filtered,
            timeout=10,
        )
        r.raise_for_status()
        return r.json()

def _post(path: str, body: dict | None = None) -> object:
    with httpx.Client() as client:
        r = client.post(
            f"{_base_url()}{path}",
            headers=_headers(),
            json=body or {},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="allstar-mcp",
    instructions=(
        "MCP server for AllStar Link node control via ASL3-API. "
        "SAFETY: Always call get_live_variables before any connect or disconnect action "
        "to check whether the node is actively transmitting (txkeyed=true) or receiving "
        "(rxkeyed=true). Interrupting an active QSO is poor operating practice. "
        "Destructive tools (disconnect_all, send_dtmf, execute_macro) require explicit "
        "user confirmation before use. Never infer confirmation — ask the user directly."
    ),
)

# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_node_status(raw: bool = False) -> object:
    """Return node statistics: uptime, keyup count, total TX time, and DTMF stats.

    Use this to get a quick health overview of the node. Suitable to call frequently.
    Set raw=True to include unparsed rpt_stats output for debugging.
    """
    return _get("/status", raw=raw if raw else None)


@mcp.tool()
def get_connected_nodes(enrich: bool = True) -> object:
    """Return the list of nodes currently linked to this node.

    With enrich=True (default), each entry includes callsign, location, and description
    from the 40k-node AllStar database. Use this to understand the current link topology
    before making any connection changes.
    """
    return _get("/nodes", enrich=enrich)


@mcp.tool()
def get_live_variables() -> object:
    """Return live app_rpt node variables sourced directly from Asterisk — no caching.

    ALWAYS call this before connect_node or disconnect_node to check:
      - rxkeyed: RF receiver is currently keyed (signal on input)
      - txkeyed: transmitter is currently active — do not disconnect during TX
      - num_links: number of connected links
      - autopatch_up: autopatch call in progress

    Returns null for fields that could not be read from AMI.
    """
    return _get("/variables")


@mcp.tool()
def get_capabilities() -> object:
    """Return static capabilities of this node and API instance.

    Machine-readable metadata: configured node number, supported features, API version.
    Intended for agent auto-configuration. Safe to call frequently — reads config only,
    never queries AMI.
    """
    return _get("/capabilities")


@mcp.tool()
def lookup_node(node_number: str) -> object:
    """Look up a node's callsign, location, and description from the local AllStar node database.

    The database contains ~40,000 nodes and is refreshed every 15 minutes from allstarlink.org.
    Served from local cache — no external HTTP call. All fields always present; unknown fields
    are null.

    Args:
        node_number: AllStar node number to look up (e.g. "637050")
    """
    return _get(f"/lookup/{node_number}")


@mcp.tool()
def get_audit_log(lines: int = 50) -> object:
    """Return recent command history from the audit log.

    Each entry has timestamp, command, and details fields. Use this to understand what
    actions have been taken recently before issuing new commands — avoids duplicating
    recent connects or re-sending DTMF that already ran.

    Args:
        lines: Number of recent log entries to return (default 50)
    """
    return _get("/audit", lines=lines)

# ---------------------------------------------------------------------------
# Low-risk control tools
# ---------------------------------------------------------------------------

@mcp.tool()
def cop_identify() -> object:
    """Play the node identification over the air. Equivalent to COP command 10.

    Triggers the node to transmit its callsign ID. Safe to use at any time; the node
    will queue the ID after any active transmission completes. Use this to verify the
    node is transmitting correctly or to manually trigger an ID.
    """
    return _post("/cop/identify")


@mcp.tool()
def cop_time() -> object:
    """Say the current time over the air. Equivalent to COP command 12.

    Causes the node to announce the current time via synthesized speech. Safe to use
    at any time. Useful for time-check announcements on a linked repeater system.
    """
    return _post("/cop/time")


@mcp.tool()
def cop_status() -> object:
    """Say the system status over the air. Equivalent to COP command 13.

    Causes the node to announce its current status (link count, connected nodes) via
    synthesized speech. Safe to use at any time.
    """
    return _post("/cop/status")


@mcp.tool()
def cop_version() -> object:
    """Say the app_rpt software version over the air. Equivalent to COP command 14.

    Causes the node to announce its app_rpt version via synthesized speech.
    """
    return _post("/cop/version")

# ---------------------------------------------------------------------------
# Medium-risk control tools (require situational awareness)
# ---------------------------------------------------------------------------

@mcp.tool()
def connect_node(
    node_number: str,
    monitor_only: bool = False,
) -> object:
    """Connect this node to a remote AllStar node.

    BEFORE CALLING: run get_live_variables to confirm txkeyed=false and rxkeyed=false.
    Connecting mid-QSO is disruptive. Also run get_connected_nodes to confirm the
    target is not already linked.

    Args:
        node_number: Remote node number to connect to (e.g. "2560")
        monitor_only: If True, connect in receive-only (monitor) mode — no TX to remote
    """
    return _post("/connect", {"node": node_number, "monitor_only": monitor_only})


@mcp.tool()
def disconnect_node(node_number: str) -> object:
    """Disconnect from a specific remote node.

    BEFORE CALLING: run get_live_variables to confirm txkeyed=false. Disconnecting
    during an active transmission drops audio mid-keying. Confirm the target node
    number with get_connected_nodes before calling.

    REQUIRES explicit user confirmation before use — do not call without the user
    actively requesting this specific disconnect.

    Args:
        node_number: Remote node number to disconnect (e.g. "2560")
    """
    return _post("/disconnect", {"node": node_number})


@mcp.tool()
def send_dtmf(sequence: str, confirmed: bool = False) -> object:
    """Send a DTMF tone sequence to the node.

    DTMF can trigger macros, link/unlink commands, or autopatch — effects depend on
    node configuration. Valid characters: 0-9, *, #

    REQUIRES confirmed=True to execute. Only set confirmed=True when the user has
    explicitly reviewed the sequence and its effects. Never infer confirmation from
    context — ask the user directly.

    BEFORE CALLING: check get_audit_log to ensure this sequence was not recently sent.
    Check get_live_variables to confirm no active transmission.

    Args:
        sequence: DTMF string to send (e.g. "1234" or "*3")
        confirmed: Must be True to execute — prevents accidental sends
    """
    if not confirmed:
        return {
            "status": "not_executed",
            "reason": (
                f"DTMF sequence '{sequence}' was not sent because confirmed=False. "
                "Ask the user to review the sequence and its effects, then set confirmed=True."
            ),
        }
    return _post("/dtmf", {"sequence": sequence, "confirmed": confirmed})


@mcp.tool()
def execute_macro(macro_number: str, confirmed: bool = False) -> object:
    """Execute a macro defined in rpt.conf.

    Macros are user-defined command sequences configured in rpt.conf. Effects vary
    by macro — they can connect/disconnect nodes, send DTMF, or trigger audio.

    REQUIRES confirmed=True to execute. Only set confirmed=True when the user has
    explicitly named the macro number and acknowledged its effects.

    Macros must exist in rpt.conf before use. Use get_audit_log to check if this
    macro was recently run.

    Args:
        macro_number: Macro number as defined in rpt.conf (e.g. "1")
        confirmed: Must be True to execute — prevents accidental macro runs
    """
    if not confirmed:
        return {
            "status": "not_executed",
            "reason": (
                f"Macro {macro_number} was not sent because confirmed=False. "
                "Ask the user to explicitly confirm this macro before setting confirmed=True."
            ),
        }
    return _post("/macro", {"macro_number": macro_number})

# ---------------------------------------------------------------------------
# High-risk / destructive tools
# ---------------------------------------------------------------------------

@mcp.tool()
def disconnect_all(confirmed: bool = False) -> object:
    """Drop ALL active node connections simultaneously.

    DESTRUCTIVE: This disconnects every linked node at once with no recovery path
    other than manually reconnecting each one. This affects all users currently
    linked through this node.

    REQUIRES confirmed=True AND explicit user instruction naming this specific action.
    Do not call in response to a general "disconnect" request — use disconnect_node
    for a specific node. Only use this when the user has said "disconnect all" or
    equivalent and confirmed the consequences.

    BEFORE CALLING: run get_connected_nodes so you can report exactly which nodes
    will be dropped.

    Args:
        confirmed: Must be True to execute — set only after explicit user confirmation
    """
    if not confirmed:
        return {
            "status": "not_executed",
            "reason": (
                "disconnect_all was not executed because confirmed=False. "
                "This action drops ALL connected nodes. Ask the user to explicitly "
                "confirm before setting confirmed=True."
            ),
        }
    return _post("/disconnect-all")

# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@mcp.resource("allstar://events/stream")
def events_stream_info() -> str:
    """SSE event stream connection details for live node state updates.

    Connect to this URL to receive real-time events:
      {base_url}/events?api_key=YOUR_KEY

    Events emitted:
      node.rxkeyed        — RF receiver keyed state changed
      node.txkeyed        — Transmitter keyed state changed
      node.variables.snapshot — Full variable state snapshot every 10s
      link.connected      — Remote node connected
      link.disconnected   — Remote node disconnected
      health.ami          — AMI connection state changed

    Keepalive comments sent every 15 seconds. Use EventSource in browser or
    curl -N to consume. Not suitable for polling — use get_live_variables for
    one-shot state reads.
    """
    base = _base_url()
    key = os.environ.get("ALLSTAR_API_KEY", "<ALLSTAR_API_KEY>")
    return (
        f"SSE stream URL: {base}/events?api_key={key}\n"
        f"Connect with: curl -N '{base}/events?api_key={key}'\n\n"
        "Events: node.rxkeyed, node.txkeyed, node.variables.snapshot, "
        "link.connected, link.disconnected, health.ami"
    )

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
