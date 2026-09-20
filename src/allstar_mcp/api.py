"""Shared HTTP adapter. ASL3-API owns policy, authorization, and radio decisions."""

import json
import os
from dataclasses import dataclass, field
from typing import TypeVar
from urllib.parse import quote, quote_plus
from uuid import uuid4

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from .models import (
    ControlResult,
    Diagnostic,
    HealthResult,
    Operation,
    OperationKind,
)

T = TypeVar("T", bound=BaseModel)
OPERATIONS = {"announce", "link_node", "unlink_node", "unlink_all"}


@dataclass(frozen=True)
class Settings:
    base_url: str = "http://127.0.0.1:8073"
    api_key: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        try:
            url = httpx.URL(self.base_url)
            valid = (
                url.scheme in {"http", "https"}
                and bool(url.host)
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
            )
        except (httpx.InvalidURL, ValueError):
            valid = False
        if not valid:
            raise ValueError(
                "ALLSTAR_API_URL must be an HTTP(S) URL without credentials, query, or fragment."
            ) from None
        if self.api_key and (
            not self.api_key.isascii()
            or any(ord(char) < 33 or ord(char) > 126 for char in self.api_key)
        ):
            raise ValueError("ALLSTAR_API_KEY must contain visible ASCII characters.")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            base_url=os.environ.get("ALLSTAR_API_URL", "http://127.0.0.1:8073").rstrip("/"),
            api_key=os.environ.get("ALLSTAR_API_KEY", ""),
        )


class APIError(Exception):
    """Sanitized API/MCP boundary error."""

    def __init__(
        self,
        code: str,
        detail: str,
        http_status: int | None = None,
        *,
        idempotency_key: str | None = None,
        outcome_uncertain: bool = False,
    ) -> None:
        self.diagnostic = Diagnostic(code=code, detail=detail, http_status=http_status)
        self.idempotency_key = idempotency_key
        self.outcome_uncertain = outcome_uncertain
        super().__init__(detail)

    def payload(self) -> dict:
        value = self.diagnostic.model_dump(exclude_none=True)
        if self.idempotency_key is not None:
            value.update(
                idempotency_key=self.idempotency_key,
                outcome_uncertain=self.outcome_uncertain,
                retried=False,
            )
        return value

    def as_text(self) -> str:
        return json.dumps(self.payload(), separators=(",", ":"), sort_keys=True)


def compatibility_error(capabilities: dict) -> str | None:
    """Require only the backend guarantees the MCP control adapter depends on."""

    expected = {
        "api_version": "1",
        "result_contract_version": "1.0",
        "backend": "app_rpt-native-ami",
        "backend_contract_version": "app_rpt-rptstatus/1",
    }
    if any(capabilities.get(key) != value for key, value in expected.items()):
        return "Backend API, result, or app_rpt contract version is incompatible."

    features = capabilities.get("features")
    if not isinstance(features, dict):
        return "Backend capability document is missing the feature contract."

    required_flags = {
        "operations": True,
        "idempotency": True,
        "single_control_owner": True,
        "automatic_control_replay": False,
    }
    if any(features.get(key) is not value for key, value in required_flags.items()):
        return "Backend operation, idempotency, or replay guarantees are incompatible."

    idempotency = capabilities.get("idempotency")
    if not isinstance(idempotency, dict) or idempotency.get("header") != "Idempotency-Key":
        return "Backend does not advertise the required Idempotency-Key contract."

    supported = features.get("supported_operations")
    if not isinstance(supported, list) or not OPERATIONS.issubset(
        item for item in supported if isinstance(item, str)
    ):
        return "Backend does not support the required semantic operations."
    return None


class API:
    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    def redact(self, text: str) -> str:
        key = self.settings.api_key
        if key:
            for secret in {key, quote(key, safe=""), quote_plus(key)}:
                text = text.replace(secret, "[REDACTED]")
        return text

    def redact_value(self, value):
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self.redact_value(item) for item in value]
        if isinstance(value, dict):
            return {
                self.redact(str(key)): self.redact_value(item)
                for key, item in value.items()
            }
        return value

    async def request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = True,
        params: dict | None = None,
        body: dict | None = None,
        idempotency_key: str | None = None,
    ) -> object:
        headers: dict[str, str] = {}
        if auth:
            if not self.settings.api_key:
                raise APIError(
                    "API_KEY_REQUIRED",
                    "Set ALLSTAR_API_KEY to your ASL3-API key.",
                )
            headers["X-API-Key"] = self.settings.api_key
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        try:
            # Exactly one HTTP request. No redirect following and no retry layer
            # are configured on the shared client.
            request_kwargs = {
                "headers": headers,
                "params": params,
            }
            if body is not None:
                request_kwargs["json"] = body
            response = await self.client.request(
                method,
                path,
                **request_kwargs,
            )
        except httpx.HTTPError:
            raise APIError(
                ("CONTROL_RESPONSE_UNCERTAIN" if idempotency_key else "API_UNREACHABLE"),
                (
                    "No reliable HTTP response. Do not repeat this control "
                    "operation automatically; inspect operation history first."
                    if idempotency_key
                    else "Could not receive an HTTP response from ASL3-API."
                ),
                idempotency_key=idempotency_key,
                outcome_uncertain=bool(idempotency_key),
            ) from None

        if not response.is_success:
            code = "API_HTTP_ERROR"
            detail = "ASL3-API returned an unsuccessful HTTP response."
            try:
                problem = response.json()
                if isinstance(problem, dict):
                    if isinstance(problem.get("code"), str):
                        code = problem["code"]
                    if isinstance(problem.get("detail"), str):
                        detail = problem["detail"]
            except ValueError:
                pass
            # A stable 4xx problem is an explicit rejection. A redirect or
            # 5xx may have been generated by an intermediary after forwarding
            # the request, so a control result is conservatively uncertain.
            uncertain = bool(idempotency_key) and not (
                400 <= response.status_code < 500
            )
            raise APIError(
                self.redact(code),
                self.redact(detail),
                response.status_code,
                idempotency_key=idempotency_key,
                outcome_uncertain=uncertain,
            ) from None

        try:
            data = response.json()
        except ValueError:
            data = None
        if data is None or (
            idempotency_key is not None and response.status_code != 202
        ):
            raise self.invalid_response(idempotency_key)
        return self.redact_value(data)

    @staticmethod
    def invalid_response(key: str | None = None) -> APIError:
        return APIError(
            "INVALID_API_RESPONSE",
            (
                "ASL3-API returned a response outside the supported result "
                "contract."
                + (
                    " Admission may have occurred; do not repeat this control "
                    "operation automatically."
                    if key
                    else ""
                )
            ),
            idempotency_key=key,
            outcome_uncertain=bool(key),
        )

    async def read(self, path: str, model: type[T], **params: int) -> T:
        data = await self.request("GET", path, params=params or None)
        try:
            return model.model_validate(data)
        except ValidationError:
            raise self.invalid_response() from None

    async def capabilities(self) -> dict:
        data = await self.request("GET", "/v1/capabilities")
        if not isinstance(data, dict):
            raise self.invalid_response()
        return data

    async def recent_operations(self, limit: int, offset: int) -> list[Operation]:
        data = await self.request(
            "GET",
            "/v1/operations",
            params={"limit": limit, "offset": offset},
        )
        try:
            return TypeAdapter(list[Operation]).validate_python(data)
        except ValidationError:
            raise self.invalid_response() from None

    async def control(
        self,
        kind: OperationKind,
        method: str,
        path: str,
        body: dict | None = None,
    ) -> ControlResult:
        # One generated key for this logical MCP invocation. There is exactly
        # one control request using it and no retry branch.
        key = uuid4().hex

        capabilities = await self.capabilities()
        error = compatibility_error(capabilities)
        if error:
            raise APIError("INCOMPATIBLE_CAPABILITIES", error)

        features = capabilities["features"]
        if features.get("control_enabled") is not True:
            raise APIError(
                "CONTROL_UNAVAILABLE",
                "ASL3-API reports control is disabled.",
            )
        if kind == "announce":
            announcements = features.get("announcements")
            if (
                not isinstance(announcements, list)
                or body is None
                or body.get("kind") not in announcements
            ):
                raise APIError(
                    "UNSUPPORTED_ANNOUNCEMENT",
                    "Backend does not support this announcement.",
                )

        data = await self.request(
            method,
            path,
            body=body,
            idempotency_key=key,
        )
        try:
            operation = Operation.model_validate(data)
        except ValidationError:
            raise self.invalid_response(key) from None

        expected_request = (
            body
            if body is not None
            else ({"node": path.rsplit("/", 1)[1]} if kind == "unlink_node" else {})
        )
        if (
            operation.kind != kind
            or operation.request != expected_request
            or operation.node != capabilities.get("node")
        ):
            raise self.invalid_response(key)
        return ControlResult(idempotency_key=key, operation=operation)

    async def health(self) -> HealthResult:
        health = HealthResult(api_key_configured=bool(self.settings.api_key))
        try:
            ping = await self.request("GET", "/ping", auth=False)
            health.api_reachable = True
            if not isinstance(ping, dict):
                raise self.invalid_response()
            for key in ("node", "callsign"):
                if isinstance(ping.get(key), str):
                    setattr(health, key, ping[key])
            if isinstance(ping.get("ami_connected"), bool):
                health.ami_connected = ping["ami_connected"]
        except APIError as exc:
            health.api_reachable = exc.diagnostic.http_status is not None
            health.errors.append(exc.diagnostic)

        try:
            capabilities = await self.capabilities()
            health.api_reachable = True
            health.auth_ok = True
            for key in (
                "node",
                "api_version",
                "result_contract_version",
                "backend_contract_version",
            ):
                if isinstance(capabilities.get(key), str):
                    setattr(health, key, capabilities[key])
            error = compatibility_error(capabilities)
            health.contract_compatible = error is None
            health.control_enabled = (
                error is None and capabilities["features"].get("control_enabled") is True
            )
            if error:
                health.errors.append(
                    Diagnostic(
                        code="INCOMPATIBLE_CAPABILITIES",
                        detail=error,
                    )
                )
        except APIError as exc:
            if exc.diagnostic.http_status is not None:
                health.api_reachable = True
            if exc.diagnostic.http_status in (401, 403) or not self.settings.api_key:
                health.auth_ok = False
            health.errors.append(exc.diagnostic)
        return health
