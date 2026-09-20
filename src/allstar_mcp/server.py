"""Client-neutral semantic MCP adapter for ASL3 Remote Platform v1."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, ValidationError

from . import __version__
from .api import API, APIError, Settings
from .models import (
    AnnounceInput,
    ControlResult,
    DirectoryEntry,
    EmptyInput,
    HealthResult,
    LinkInput,
    LookupInput,
    NodeState,
    Operation,
    OperationStatusInput,
    RecentOperations,
    RecentOperationsInput,
    UnlinkInput,
)


def _lifespan_factory(
    fixed_settings: Settings | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
):
    @asynccontextmanager
    async def lifespan(_server: Server):
        """Own one shared non-retrying HTTP client for the stdio server lifetime."""

        settings = fixed_settings or Settings.from_env()
        transport = http_transport or httpx.AsyncHTTPTransport(retries=0)
        async with httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        ) as client:
            yield API(client, settings)

    return lifespan


READ = types.ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
)
CONTROL = types.ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
)
DESTRUCTIVE = types.ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
)


class ToolSpec:
    def __init__(
        self,
        name: str,
        description: str,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
        annotations: types.ToolAnnotations,
    ) -> None:
        self.name = name
        self.description = description
        self.input_model = input_model
        self.output_model = output_model
        self.annotations = annotations
        self.read_only = annotations.read_only_hint is True

    def definition(self) -> types.Tool:
        return types.Tool(
            name=self.name,
            description=self.description,
            inputSchema=self.input_model.model_json_schema(),
            outputSchema=self.output_model.model_json_schema(),
            annotations=self.annotations,
        )

    def mcp_tool(self) -> types.Tool:
        return self.definition()


SPECS: dict[str, ToolSpec] = {
    "health_check": ToolSpec(
        "health_check",
        (
            "Check API reachability, authentication, AMI health, and v1 contract "
            "compatibility. This remains useful when authentication is broken."
        ),
        EmptyInput,
        HealthResult,
        READ,
    ),
    "get_node_state": ToolSpec(
        "get_node_state",
        (
            "Get a fresh authoritative node snapshot. Unknown or incomplete "
            "evidence remains explicitly unknown."
        ),
        EmptyInput,
        NodeState,
        READ,
    ),
    "lookup_node": ToolSpec(
        "lookup_node",
        (
            "Look up public directory metadata for a canonical AllStar node "
            "number. Directory absence does not make a private/static target invalid."
        ),
        LookupInput,
        DirectoryEntry,
        READ,
    ),
    "get_recent_operations": ToolSpec(
        "get_recent_operations",
        ("Read recent durable control operations, including dispatch and effect uncertainty."),
        RecentOperationsInput,
        RecentOperations,
        READ,
    ),
    "get_operation_status": ToolSpec(
        "get_operation_status",
        "Read one durable operation by its operation ID.",
        OperationStatusInput,
        Operation,
        READ,
    ),
    "announce": ToolSpec(
        "announce",
        (
            "Request one fixed semantic announcement: identify, time, status, "
            "or version. ASL3-API performs admission policy and returns an operation."
        ),
        AnnounceInput,
        ControlResult,
        CONTROL,
    ),
    "link_node": ToolSpec(
        "link_node",
        (
            "Request a direct AllStar link in transceive or monitor mode. "
            "ASL3-API is the authority for traffic policy and safe admission."
        ),
        LinkInput,
        ControlResult,
        CONTROL,
    ),
    "unlink_node": ToolSpec(
        "unlink_node",
        (
            "Remove one exact direct AllStar link, including a permanent link. "
            "ASL3-API owns authorization, policy, dispatch, and verification."
        ),
        UnlinkInput,
        ControlResult,
        DESTRUCTIVE,
    ),
    "unlink_all": ToolSpec(
        "unlink_all",
        (
            "Remove all direct links, including permanent links. This is "
            "destructive and returns a durable ASL3-API operation."
        ),
        EmptyInput,
        ControlResult,
        DESTRUCTIVE,
    ),
}

TOOLS = SPECS


def _success(model: BaseModel) -> types.CallToolResult:
    payload = model.model_dump(mode="json")
    text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=payload,
        isError=False,
    )


def _error(code: str, detail: str, **extra: Any) -> types.CallToolResult:
    payload: dict[str, Any] = {"code": code, "detail": detail}
    payload.update(extra)
    # Normalize tuples and other JSON-compatible Pydantic values so the
    # structured and text representations are byte-for-byte equivalent.
    payload = json.loads(json.dumps(payload, default=str))
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=json.dumps(
                    payload,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        ],
        structuredContent=payload,
        isError=True,
    )


async def list_tools(
    _ctx: ServerRequestContext,
    _params: types.PaginatedRequestParams | None,
) -> types.ListToolsResult:
    return types.ListToolsResult(tools=[spec.definition() for spec in TOOLS.values()])


async def call_tool(
    ctx: ServerRequestContext,
    params: types.CallToolRequestParams,
) -> types.CallToolResult:
    if params.task is not None:
        return _error(
            "TASKS_UNSUPPORTED",
            "MCP Tasks are not supported by this adapter.",
        )

    spec = TOOLS.get(params.name)
    if spec is None:
        return _error("UNKNOWN_TOOL", f"Unknown tool: {params.name}")

    try:
        args = spec.input_model.model_validate(params.arguments or {})
    except ValidationError as exc:
        return _error(
            "INVALID_ARGUMENTS",
            "Tool arguments do not match the declared schema.",
            errors=exc.errors(include_url=False, include_input=False),
        )

    api = ctx.lifespan_context
    if not isinstance(api, API):
        return _error(
            "MCP_RUNTIME_ERROR",
            "ASL3 API client lifespan is unavailable.",
        )

    try:
        if params.name == "health_check":
            result = await api.health()
        elif params.name == "get_node_state":
            result = await api.read("/v1/node/state", NodeState)
        elif params.name == "lookup_node":
            result = await api.read(
                f"/v1/directory/{args.node}",
                DirectoryEntry,
            )
        elif params.name == "get_recent_operations":
            result = RecentOperations(
                operations=await api.recent_operations(
                    args.limit,
                    args.offset,
                )
            )
        elif params.name == "get_operation_status":
            result = await api.read(
                f"/v1/operations/{args.operation_id}",
                Operation,
            )
        elif params.name == "announce":
            result = await api.control(
                "announce",
                "POST",
                "/v1/announcements",
                {"kind": args.kind},
            )
        elif params.name == "link_node":
            result = await api.control(
                "link_node",
                "POST",
                "/v1/links",
                {"node": args.node, "mode": args.mode},
            )
        elif params.name == "unlink_node":
            result = await api.control(
                "unlink_node",
                "DELETE",
                f"/v1/links/{args.node}",
            )
        elif params.name == "unlink_all":
            result = await api.control(
                "unlink_all",
                "DELETE",
                "/v1/links",
            )
        else:  # pragma: no cover - SPECS and dispatch are kept in lockstep.
            return _error("UNKNOWN_TOOL", f"Unknown tool: {params.name}")
    except APIError as exc:
        payload = exc.payload()
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=exc.as_text())],
            structuredContent=payload,
            isError=True,
        )

    if not isinstance(result, spec.output_model):
        return _error(
            "MCP_RUNTIME_ERROR",
            "Tool produced an unexpected result type.",
        )
    return _success(result)


def create_server(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> Server:
    if transport is not None and http_transport is not None:
        raise ValueError("Specify only one HTTP transport.")
    selected_transport = transport if transport is not None else http_transport
    return Server(
        "allstar-mcp",
        version=__version__,
        description=(
            "Semantic MCP interface to ASL3 Remote Platform v1. "
            "The REST API remains the radio control authority."
        ),
        instructions=(
            "Use read tools to understand current state and durable operation results. "
            "Control tools return ASL3-API operations, not claims that a radio effect "
            "already occurred. Never reinterpret UNKNOWN as false or success."
        ),
        lifespan=_lifespan_factory(settings, selected_transport),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


server = create_server()



async def run_stdio() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
