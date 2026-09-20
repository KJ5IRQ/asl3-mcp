"""ASL3-API v1 result models and MCP tool input/output envelopes."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NODE_PATTERN = r"^[1-9][0-9]{0,5}$"
Node = Annotated[str, StringConstraints(strict=True, pattern=NODE_PATTERN)]
OperationId = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_-]{1,128}$")]
# identify and status are the only kinds ASL3-API supports. time and version
# were withdrawn: app_rpt sends them as link telemetry text and the receiving
# node decides whether anything is spoken, so an audible result cannot be
# promised. Narrowed here as well as in the backend so the published tool
# schema never offers a client a value that will be refused.
AnnouncementKind = Literal["identify", "status"]
LinkMode = Literal["transceive", "monitor"]
OperationKind = Literal["link_node", "unlink_node", "unlink_all", "announce"]


class APIModel(BaseModel):
    """Accept additive backend fields while keeping known fields strict."""

    model_config = ConfigDict(extra="allow", strict=True)


class ToolInput(BaseModel):
    """Reject unknown MCP tool arguments instead of silently ignoring them."""

    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyInput(ToolInput):
    pass


class LookupInput(ToolInput):
    node: Node


class RecentOperationsInput(ToolInput):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class OperationStatusInput(ToolInput):
    operation_id: OperationId


class AnnounceInput(ToolInput):
    kind: AnnouncementKind


class LinkInput(ToolInput):
    node: Node
    mode: LinkMode = "transceive"


class UnlinkInput(ToolInput):
    node: Node


class DirectLink(APIModel):
    node: str
    allstar_node: Node | None = None
    mode: Literal["T", "R", "L", "C", "UNKNOWN"]
    keyed: bool
    connection_status: Literal["ESTABLISHED", "CONNECTING"]


class NodeState(APIModel):
    node: Node
    observed_at: str
    connection_epoch: str | None = None
    state_status: Literal["COMPLETE", "STATE_UNKNOWN"]
    traffic_state: Literal["ACTIVE", "CLEAR", "UNKNOWN"]
    complete: bool
    rx_keyed: bool | None = Field(
        default=None,
        description=(
            "app_rpt RPT_RXKEYED: receiver logical state. null when unknown."
        ),
    )
    tx_keyed: bool | None = Field(
        default=None,
        description=(
            "app_rpt RPT_TXKEYED: main/local TX logical state. It is not proof "
            "of RF, of a physically keyed transmitter, or of audio crossing a "
            "native link. On a radioless Local/pseudo hub it can be true while "
            "nothing reaches the links. ASL3-API still treats true as ACTIVE "
            "traffic for safety. null when unknown."
        ),
    )
    direct_links: list[DirectLink] | None = None
    reasons: list[str] = Field(default_factory=list)
    source: str = "app_rpt/RptStatus/XStat+SawStat"


class Operation(APIModel):
    id: OperationId
    node: Node
    kind: OperationKind
    request: dict
    credential: str
    created_at: str
    updated_at: str
    dispatch_status: Literal[
        "QUEUED",
        "DISPATCH_STARTED",
        "ACKNOWLEDGED",
        "REJECTED",
        "NOT_DISPATCHED",
        "OUTCOME_UNKNOWN",
    ]
    effect_status: Literal[
        "PENDING",
        "NOT_ATTEMPTED",
        "OBSERVED_SATISFIED",
        "OBSERVED_PARTIAL",
        "OBSERVED_UNSATISFIED",
        "NOT_APPLICABLE",
        "UNKNOWN",
    ]
    terminal: bool
    semantic_error: str | None = None
    evidence: NodeState | None = None


class DirectoryEntry(APIModel):
    node: Node
    callsign: str | None
    description: str | None
    location: str | None
    source: str
    last_updated: float | None
    authoritative_for_target_validity: Literal[False]


class RecentOperations(BaseModel):
    operations: list[Operation]


class ControlResult(BaseModel):
    """One admitted control request and the key that identifies that logical attempt."""

    idempotency_key: str
    operation: Operation


class Diagnostic(BaseModel):
    code: str
    detail: str
    http_status: int | None = None


class HealthResult(BaseModel):
    api_reachable: bool = False
    auth_ok: bool | None = None
    api_key_configured: bool
    ami_connected: bool | None = None
    node: str | None = None
    callsign: str | None = None
    api_version: str | None = None
    result_contract_version: str | None = None
    backend_contract_version: str | None = None
    contract_compatible: bool = False
    control_enabled: bool = False
    errors: list[Diagnostic] = Field(default_factory=list)
