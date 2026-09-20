"""Offline contract tests using official SDK 2.2.0 and httpx MockTransport."""

import asyncio
import copy
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import httpx
import jsonschema
import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from allstar_mcp.api import Settings
from allstar_mcp.server import TOOLS, call_tool, create_server

KEY = "test-secret+/credential"
CAPABILITIES = {
    "api_version": "1",
    "result_contract_version": "1.0",
    "backend": "app_rpt-native-ami",
    "backend_contract_version": "app_rpt-rptstatus/1",
    "node": "637050",
    "features": {
        "operations": True,
        "idempotency": True,
        "single_control_owner": True,
        "automatic_control_replay": False,
        "control_enabled": True,
        "supported_operations": ["announce", "link_node", "unlink_node", "unlink_all"],
        "announcements": ["identify", "status"],
    },
    "idempotency": {"header": "Idempotency-Key"},
}
STATE = {
    "node": "637050",
    "observed_at": "2026-09-19T12:00:00Z",
    "state_status": "STATE_UNKNOWN",
    "traffic_state": "UNKNOWN",
    "complete": False,
    "rx_keyed": None,
    "tx_keyed": None,
    "direct_links": None,
    "reasons": ["AMI disconnected"],
}
DIRECTORY = {
    "node": "2560",
    "callsign": None,
    "description": None,
    "location": None,
    "source": "directory",
    "last_updated": None,
    "authoritative_for_target_validity": False,
}
CONTROLS = [
    ("announce", {"kind": "identify"}, "POST", "/v1/announcements", {"kind": "identify"}),
    ("link_node", {"node": "2560"}, "POST", "/v1/links", {"node": "2560", "mode": "transceive"}),
    (
        "link_node",
        {"node": "2560", "mode": "monitor"},
        "POST",
        "/v1/links",
        {"node": "2560", "mode": "monitor"},
    ),
    ("unlink_node", {"node": "2560"}, "DELETE", "/v1/links/2560", None),
    ("unlink_all", {}, "DELETE", "/v1/links", None),
]


def operation(kind="link_node", request=None):
    return {
        "id": "op_123",
        "node": "637050",
        "kind": kind,
        "request": request if request is not None else {"node": "2560", "mode": "transceive"},
        "credential": "operator-label",
        "created_at": "2026-09-19T12:00:00Z",
        "updated_at": "2026-09-19T12:00:00Z",
        "dispatch_status": "QUEUED",
        "effect_status": "PENDING",
        "terminal": False,
    }


class Backend:
    def __init__(self):
        self.requests = []
        self.capabilities = copy.deepcopy(CAPABILITIES)
        self.control_reply = None
        self.read_reply = None
        self.capability_reply = None

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/ping":
            assert "X-API-Key" not in request.headers
            return httpx.Response(200, json={"node": "637050", "ami_connected": False})
        assert request.headers["X-API-Key"] == KEY
        if path == "/v1/capabilities":
            return self.capability_reply or httpx.Response(200, json=self.capabilities)
        if request.method != "GET":
            if isinstance(self.control_reply, Exception):
                raise self.control_reply
            if self.control_reply is not None:
                return self.control_reply
            if request.method == "DELETE":
                kind = "unlink_all" if path == "/v1/links" else "unlink_node"
                body = {} if kind == "unlink_all" else {"node": path.rsplit("/", 1)[1]}
            else:
                kind = "announce" if path == "/v1/announcements" else "link_node"
                body = json.loads(request.content)
            return httpx.Response(202, json=operation(kind, body))
        if self.read_reply is not None:
            return self.read_reply
        if path == "/v1/node/state":
            return httpx.Response(200, json=STATE)
        if path == "/v1/directory/2560":
            return httpx.Response(200, json=DIRECTORY)
        if path == "/v1/operations":
            return httpx.Response(200, json=[operation()])
        if path == "/v1/operations/op_123":
            return httpx.Response(200, json=operation())
        raise AssertionError(f"Unexpected endpoint: {request.method} {path}")


@asynccontextmanager
async def running(backend, key=KEY):
    server = create_server(Settings(api_key=key), transport=httpx.MockTransport(backend))
    async with server.lifespan(server) as api:
        yield SimpleNamespace(lifespan_context=api)
    assert api.client.is_closed


async def invoke(context, name, arguments=None, **kwargs):
    result = await call_tool(
        context,
        types.CallToolRequestParams(name=name, arguments=arguments, **kwargs),
    )
    assert json.loads(result.content[0].text) == result.structured_content
    if not result.is_error:
        jsonschema.validate(result.structured_content, TOOLS[name].definition().output_schema)
    return result


def run_call(backend, name, arguments=None, key=KEY, **kwargs):
    async def run():
        async with running(backend, key) as context:
            return await invoke(context, name, arguments, **kwargs)

    return asyncio.run(run())


def test_discovery_surface():
    assert set(TOOLS) == {
        "health_check",
        "get_node_state",
        "lookup_node",
        "get_recent_operations",
        "get_operation_status",
        "announce",
        "link_node",
        "unlink_node",
        "unlink_all",
    }
    capabilities = create_server().get_capabilities().model_dump(exclude_none=True)
    assert set(capabilities) == {"tools"}
    for name, spec in TOOLS.items():
        tool = spec.definition()
        assert tool.input_schema["additionalProperties"] is False
        assert tool.output_schema
        assert tool.execution is None
        assert tool.annotations.read_only_hint == (name not in {c[0] for c in CONTROLS})
        assert tool.annotations.destructive_hint == (name in {"unlink_node", "unlink_all"})
        if not spec.read_only:
            assert tool.annotations.idempotent_hint is False


@pytest.mark.parametrize("name,args,method,path,body", CONTROLS)
def test_one_key_one_control_request(name, args, method, path, body):
    backend = Backend()
    with patch("allstar_mcp.api.uuid4", return_value=UUID(int=123)) as uuid:
        result = run_call(backend, name, args)
    assert not result.is_error
    uuid.assert_called_once_with()
    assert [(r.method, r.url.path) for r in backend.requests] == [
        ("GET", "/v1/capabilities"),
        (method, path),
    ]
    request = backend.requests[1]
    assert "Idempotency-Key" not in backend.requests[0].headers
    assert request.headers["Idempotency-Key"] == UUID(int=123).hex
    assert result.structured_content["idempotency_key"] == UUID(int=123).hex
    assert (json.loads(request.content) if request.content else None) == body
    assert result.structured_content["operation"]["dispatch_status"] == "QUEUED"
    assert result.structured_content["operation"]["effect_status"] == "PENDING"


def test_lifespan_reuses_client_and_identical_intents_have_distinct_keys():
    backend = Backend()

    async def run():
        async with running(backend) as context:
            client = context.lifespan_context.client
            await invoke(context, "get_node_state")
            first = await invoke(context, "unlink_all")
            second = await invoke(context, "unlink_all")
            assert context.lifespan_context.client is client
            assert (
                first.structured_content["idempotency_key"]
                != (second.structured_content["idempotency_key"])
            )

    asyncio.run(run())
    assert len(backend.requests) == 5


@pytest.mark.parametrize(
    "name,args,path",
    [
        ("get_node_state", {}, "/v1/node/state"),
        ("lookup_node", {"node": "2560"}, "/v1/directory/2560"),
        ("get_recent_operations", {"limit": 7, "offset": 2}, "/v1/operations"),
        ("get_operation_status", {"operation_id": "op_123"}, "/v1/operations/op_123"),
    ],
)
def test_read_routing_and_results(name, args, path):
    backend = Backend()
    result = run_call(backend, name, args)
    assert not result.is_error
    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.method == "GET" and request.url.path == path
    assert "Idempotency-Key" not in request.headers
    if name == "get_recent_operations":
        assert dict(request.url.params) == {"limit": "7", "offset": "2"}
    if name == "get_node_state":
        assert result.structured_content["traffic_state"] == "UNKNOWN"
        assert result.structured_content["rx_keyed"] is None


@pytest.mark.parametrize(
    "node",
    ["", "0", "0123", "1234567", "１２３", "١٢٣", "1\n", " 1", "1/2", "../1", "-1", 123, True],
)
@pytest.mark.parametrize("name", ["lookup_node", "link_node", "unlink_node"])
def test_invalid_nodes_do_not_reach_backend(name, node):
    backend = Backend()
    result = run_call(backend, name, {"node": node})
    assert result.is_error and result.structured_content["code"] == "INVALID_ARGUMENTS"
    assert not backend.requests


@pytest.mark.parametrize(
    "name,args",
    [
        ("announce", {"kind": "dtmf"}),
        ("announce", {}),
        ("link_node", {"node": "2560", "mode": "permanent"}),
        ("get_recent_operations", {"limit": 0}),
        ("get_recent_operations", {"limit": 101}),
        ("get_recent_operations", {"limit": True}),
        ("get_recent_operations", {"offset": -1}),
        ("get_recent_operations", {"limit": "20"}),
        ("get_operation_status", {"operation_id": "../secret"}),
        ("get_operation_status", {"operation_id": "id?key=secret"}),
        ("get_operation_status", {"operation_id": "x" * 129}),
    ],
)
def test_invalid_arguments(name, args):
    backend = Backend()
    assert run_call(backend, name, args).is_error
    assert not backend.requests


@pytest.mark.parametrize("forbidden", ["confirmed", "dry_run", "override", "force", "emergency"])
@pytest.mark.parametrize("name,args,method,path,body", CONTROLS)
def test_no_bypass_arguments(forbidden, name, args, method, path, body):
    backend = Backend()
    assert run_call(backend, name, {**args, forbidden: True}).is_error
    assert not backend.requests


@pytest.mark.parametrize(
    "name",
    [
        "get_capabilities",
        "wait_for_clear",
        "send_dtmf",
        "execute_macro",
        "cop_identify",
        "raw_rest",
        "connect_node",
    ],
)
def test_removed_tools(name):
    backend = Backend()
    assert run_call(backend, name).structured_content["code"] == "UNKNOWN_TOOL"
    assert not backend.requests


def test_task_rejected_before_control():
    backend = Backend()
    result = run_call(backend, "unlink_all", task=types.TaskMetadata(ttl=1000))
    assert result.structured_content["code"] == "TASKS_UNSUPPORTED"
    assert not backend.requests


@pytest.mark.parametrize(
    "section,field,value",
    [
        (None, "api_version", "2"),
        (None, "result_contract_version", "0.1"),
        (None, "backend", "legacy"),
        (None, "backend_contract_version", None),
        (None, "features", []),
        ("features", "operations", False),
        ("features", "idempotency", None),
        ("features", "single_control_owner", 1),
        ("features", "automatic_control_replay", True),
        ("features", "supported_operations", ["link_node"]),
        ("idempotency", "header", "X-Key"),
    ],
)
def test_incompatible_capabilities_refuse_control(section, field, value):
    backend = Backend()
    target = backend.capabilities if section is None else backend.capabilities[section]
    target[field] = value
    result = run_call(backend, "unlink_all")
    assert result.structured_content["code"] == "INCOMPATIBLE_CAPABILITIES"
    assert [r.url.path for r in backend.requests] == ["/v1/capabilities"]


@pytest.mark.parametrize(
    "name,args,feature,value,code",
    [
        ("unlink_all", {}, "control_enabled", False, "CONTROL_UNAVAILABLE"),
        # `status` is valid in the MCP schema, so this proves the backend
        # capability gate fires on its own rather than riding on schema
        # enforcement: the backend advertises only `identify`.
        ("announce", {"kind": "status"}, "announcements", ["identify"], "UNSUPPORTED_ANNOUNCEMENT"),
    ],
)
def test_backend_control_availability(name, args, feature, value, code):
    backend = Backend()
    backend.capabilities["features"][feature] = value
    assert run_call(backend, name, args).structured_content["code"] == code
    assert len(backend.requests) == 1


@pytest.mark.parametrize("name,args,method,path,body", CONTROLS)
@pytest.mark.parametrize(
    "exception",
    [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError, httpx.DecodingError],
)
def test_uncertain_transport_never_retries(name, args, method, path, body, exception):
    backend = Backend()
    backend.control_reply = exception(f"secret {KEY}")
    result = run_call(backend, name, args)
    assert result.is_error
    data = result.structured_content
    assert data["code"] == "CONTROL_RESPONSE_UNCERTAIN"
    assert data["outcome_uncertain"] is True and data["retried"] is False
    assert data["idempotency_key"] == backend.requests[-1].headers["Idempotency-Key"]
    assert KEY not in result.model_dump_json()
    assert len(backend.requests) == 2


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(202, content=b"not JSON"),
        httpx.Response(202, json={}),
        httpx.Response(200, json=operation()),
        httpx.Response(204),
        httpx.Response(503, json={"code": "BACKEND_DOWN", "detail": "Try status later"}),
        httpx.Response(307, headers={"Location": "http://other.invalid/v1/links"}),
    ],
)
def test_unreliable_control_response_is_uncertain_and_not_retried(response):
    backend = Backend()
    backend.control_reply = response
    result = run_call(backend, "link_node", {"node": "2560"})
    assert result.is_error and result.structured_content["outcome_uncertain"] is True
    assert len(backend.requests) == 2


@pytest.mark.parametrize(
    "field,value",
    [("kind", "unlink_all"), ("node", "123"), ("request", {"node": "123", "mode": "monitor"})],
)
def test_mismatched_admission_not_reported_as_success(field, value):
    backend = Backend()
    data = operation()
    data[field] = value
    backend.control_reply = httpx.Response(202, json=data)
    result = run_call(backend, "link_node", {"node": "2560"})
    assert result.structured_content["code"] == "INVALID_API_RESPONSE"
    assert result.structured_content["outcome_uncertain"] is True
    assert len(backend.requests) == 2


def test_api_problem_preserved_and_credentials_redacted():
    backend = Backend()
    backend.control_reply = httpx.Response(
        409,
        json={
            "code": "TRAFFIC_ACTIVE",
            "detail": f"Busy for credential {KEY}",
            "debug": {"api_key": KEY},
        },
    )
    result = run_call(backend, "unlink_all")
    data = result.structured_content
    assert data["code"] == "TRAFFIC_ACTIVE"
    assert data["detail"] == "Busy for credential [REDACTED]"
    assert data["http_status"] == 409 and data["outcome_uncertain"] is False
    assert KEY not in result.model_dump_json()
    assert len(backend.requests) == 2


def test_success_payload_cannot_echo_configured_key():
    backend = Backend()
    backend.read_reply = httpx.Response(200, json={**STATE, "extra": {"echo": [KEY]}})
    result = run_call(backend, "get_node_state")
    assert not result.is_error
    assert result.structured_content["extra"]["echo"] == ["[REDACTED]"]


@pytest.mark.parametrize("status", [401, 403, 503])
def test_health_survives_backend_auth_failure(status):
    backend = Backend()
    backend.capability_reply = httpx.Response(status, json={"code": "DENIED", "detail": KEY})
    result = run_call(backend, "health_check")
    data = result.structured_content
    assert not result.is_error and data["api_reachable"] is True
    assert data["auth_ok"] is (False if status in (401, 403) else None)
    assert data["control_enabled"] is False
    assert data["errors"][0]["code"] == "DENIED"
    assert KEY not in result.model_dump_json()


def test_missing_key_keeps_health_available_but_prevents_authenticated_requests():
    backend = Backend()
    health = run_call(backend, "health_check", key="").structured_content
    assert health["api_reachable"] and health["auth_ok"] is False
    assert health["errors"][0]["code"] == "API_KEY_REQUIRED"
    assert [r.url.path for r in backend.requests] == ["/ping"]
    backend.requests.clear()
    assert run_call(backend, "unlink_all", key="").is_error
    assert not backend.requests


def test_health_offline_does_not_expose_transport_error():
    def offline(request):
        raise httpx.ConnectError(KEY)

    result = run_call(offline, "health_check")
    assert not result.is_error
    assert result.structured_content["api_reachable"] is False
    assert result.structured_content["auth_ok"] is None
    assert len(result.structured_content["errors"]) == 2
    assert KEY not in result.model_dump_json()


def test_health_incompatible_backend_and_reads_still_work():
    backend = Backend()
    backend.capabilities["api_version"] = "legacy"
    health = run_call(backend, "health_check").structured_content
    assert health["api_reachable"] and health["auth_ok"]
    assert not health["contract_compatible"] and not health["control_enabled"]
    assert not run_call(backend, "get_node_state").is_error


@pytest.mark.parametrize("payload", [None, [], {"node": "bad"}, {**STATE, "complete": "false"}])
def test_invalid_read_response(payload):
    backend = Backend()
    backend.read_reply = httpx.Response(200, content=json.dumps(payload))
    assert run_call(backend, "get_node_state").structured_content["code"] == "INVALID_API_RESPONSE"


@pytest.mark.parametrize(
    "url",
    [
        "file:///secret",
        "http://user:pass@host",
        "http://host?key=x",
        "http://host#secret",
        "not-a-url",
    ],
)
def test_settings_reject_unsafe_urls_without_echo(url):
    with pytest.raises(ValueError) as exc:
        Settings(base_url=url)
    assert url not in str(exc.value)


def test_settings_defaults_and_secret_repr(monkeypatch):
    monkeypatch.delenv("ALLSTAR_API_URL", raising=False)
    monkeypatch.setenv("ALLSTAR_API_KEY", KEY)
    settings = Settings.from_env()
    assert settings.base_url == "http://127.0.0.1:8073"
    assert KEY not in repr(settings)


def test_real_stdio_sdk_handshake_and_tool_error():
    source = str(Path(__file__).resolve().parents[1] / "src")

    async def run():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "allstar_mcp.server"],
            env={**os.environ, "PYTHONPATH": source, "ALLSTAR_API_KEY": ""},
        )
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams, read_timeout_seconds=5) as session:
                initialized = await session.initialize()
                assert initialized.capabilities.tools is not None
                assert initialized.capabilities.resources is None
                assert initialized.capabilities.prompts is None
                assert initialized.capabilities.tasks is None
                listed = await session.list_tools()
                assert {t.name for t in listed.tools} == set(TOOLS)
                result = await session.call_tool("link_node", {"node": "bad"})
                assert result.is_error
                assert result.structured_content["code"] == "INVALID_ARGUMENTS"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Withdrawn announcement kinds
#
# `time` and `version` are withdrawn. app_rpt delivers them as link telemetry
# text and the receiving node decides whether anything is spoken, so neither
# ASL3-API nor this adapter can promise an audible announcement. The published
# tool schema must not offer a value the backend will refuse.
# ---------------------------------------------------------------------------

WITHDRAWN_KINDS = ["time", "version"]


def announce_kind_enum():
    """The announcement enum exactly as an MCP client receives it."""
    schema = TOOLS["announce"].definition().input_schema
    return schema["properties"]["kind"]["enum"]


def test_published_announce_enum_is_exactly_identify_and_status():
    assert announce_kind_enum() == ["identify", "status"]


@pytest.mark.parametrize("kind", WITHDRAWN_KINDS)
def test_published_announce_enum_omits_withdrawn_kinds(kind):
    assert kind not in announce_kind_enum()


@pytest.mark.parametrize("kind", WITHDRAWN_KINDS)
def test_announce_description_does_not_advertise_withdrawn_kinds(kind):
    description = TOOLS["announce"].definition().description
    assert kind not in description.lower()


def test_announce_description_names_the_supported_kinds():
    description = TOOLS["announce"].definition().description.lower()
    assert "identify" in description
    assert "status" in description


@pytest.mark.parametrize("kind", WITHDRAWN_KINDS)
def test_withdrawn_kind_is_rejected_before_any_backend_call(kind):
    """Schema enforcement must stop it without touching the backend."""
    backend = Backend()
    result = run_call(backend, "announce", {"kind": kind})
    assert result.is_error
    assert result.structured_content["code"] == "INVALID_ARGUMENTS"
    assert backend.requests == []


@pytest.mark.parametrize("kind", ["identify", "status"])
def test_supported_kinds_still_dispatch(kind):
    backend = Backend()
    result = run_call(backend, "announce", {"kind": kind})
    assert not result.is_error
    assert [r.url.path for r in backend.requests] == [
        "/v1/capabilities",
        "/v1/announcements",
    ]


@pytest.mark.parametrize("kind", WITHDRAWN_KINDS)
def test_historical_withdrawn_operation_is_still_readable(kind):
    """Withdrawal narrows what can be requested, not what was recorded.

    The hub ran with these kinds supported, so the backend can still return an
    operation whose request names one. Reading it must not fail.
    """
    backend = Backend()
    backend.read_reply = httpx.Response(
        200, json=operation("announce", {"kind": kind})
    )
    result = run_call(backend, "get_operation_status", {"operation_id": "op_123"})
    assert not result.is_error
    assert result.structured_content["kind"] == "announce"
    assert result.structured_content["request"] == {"kind": kind}
