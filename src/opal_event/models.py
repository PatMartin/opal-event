"""Pydantic models mirroring the schemas in ``opal_event.yaml``.

Field names, types, constraints, and ``additionalProperties: false`` semantics are kept in lock-step with the
OpenAPI contract. The contract is authoritative; do not add fields here that are not present there.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TOPIC_PATTERN = r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*$"
RULE_ID_PATTERN = r"^[a-zA-Z][a-zA-Z0-9_-]*$"
MODULE_PATTERN = r"^[a-zA-Z_]\w*(\.[a-zA-Z_]\w*)*$"
FUNCTION_PATTERN = r"^[a-zA-Z_]\w*$"
MAX_EVENT_BYTES = 1_048_576


class Strict(BaseModel):
    """Base model that rejects unknown properties, matching ``additionalProperties: false``."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------------------


class EventInput(Strict):
    """REST event input. Validation mirrors the ZeroMQ observer's wire rules so a 202 receipt
    never precedes an observer rejection: supplied ``id`` must be a UUID, ``timestamp`` an
    RFC3339 date-time, ``topic``/``source`` keep their documented constraints, and ``data`` is
    required (explicit JSON null is allowed). Omitted id/timestamp are generated downstream."""

    id: str | None = Field(default=None)
    topic: Annotated[str, Field(pattern=TOPIC_PATTERN, max_length=255)]
    source: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    timestamp: str | None = None
    data: Any

    @field_validator("id")
    @classmethod
    def _id_is_uuid(cls, v: str | None) -> str | None:
        if v is not None:
            from opal_event.normalization import is_uuid_string

            if not is_uuid_string(v):
                raise ValueError("id must be a UUID")
        return v

    @field_validator("timestamp")
    @classmethod
    def _timestamp_is_rfc3339(cls, v: str | None) -> str | None:
        if v is not None:
            from opal_event.normalization import is_rfc3339_datetime

            if not is_rfc3339_datetime(v):
                raise ValueError("timestamp must be an RFC3339 date-time")
        return v


class Event(Strict):
    id: str
    topic: Annotated[str, Field(pattern=TOPIC_PATTERN, max_length=255)]
    source: Annotated[str, Field(min_length=1, max_length=128)]
    timestamp: str
    data: Any = None


class PublishReceipt(Strict):
    id: str
    status: Literal["accepted"]
    published_at: str


class ListEvents(Strict):
    items: list[Event]
    next_cursor: str | None = None


class TopicList(Strict):
    items: list[str]


# --------------------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------------------

ConditionOperator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "exists", "contains"]


class Condition(Strict):
    path: str
    operator: ConditionOperator
    value: Any = None


class Rule(Strict):
    id: Annotated[str, Field(pattern=RULE_ID_PATTERN)]
    enabled: bool = True
    topic: str
    conditions: list[Condition]
    actions: list[Annotated[str, Field(min_length=1)]]


class RulePatch(Strict):
    enabled: bool | None = None
    topic: str | None = None
    conditions: list[Condition] | None = None
    actions: list[Annotated[str, Field(min_length=1)]] | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> RulePatch:
        if not self.model_fields_set:
            raise ValueError("at least one property is required")
        return self


class ListRules(Strict):
    items: list[Rule]
    next_cursor: str | None = None


class RuleTestInput(Strict):
    event: EventInput


class RuleTestResult(Strict):
    matched: bool
    action_ids: list[str]
    checks: list[dict[str, Any]]


# --------------------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------------------

ShellMode = Literal["exec", "sh", "bash", "cmd", "powershell"]
_SCRIPT_MODES = {"sh", "bash", "cmd", "powershell"}


class ShellAction(Strict):
    id: str
    type: Literal["shell"]
    enabled: bool = True
    mode: ShellMode
    executable: str | None = None
    script_path: str | None = None
    args: list[str] = Field(default_factory=list)
    stdin: Literal["event_json", "none"] = "event_json"
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)] | None = None

    @model_validator(mode="after")
    def _mode_requirements(self) -> ShellAction:
        if self.mode == "exec" and not self.executable:
            raise ValueError("executable is required when mode is 'exec'")
        if self.mode in _SCRIPT_MODES and not self.script_path:
            raise ValueError("script_path is required for interpreter modes")
        return self


class HttpAction(Strict):
    id: str
    type: Literal["http"]
    enabled: bool = True
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    url: str
    headers: dict[str, str] | None = None
    body: Any = None
    body_mode: Literal["template", "event_json", "none"]
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)] | None = None


class PythonAction(Strict):
    id: str
    type: Literal["python"]
    enabled: bool = True
    module: Annotated[str, Field(pattern=MODULE_PATTERN)]
    function: Annotated[str, Field(pattern=FUNCTION_PATTERN)]
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)] | None = None


Action = Annotated[ShellAction | HttpAction | PythonAction, Field(discriminator="type")]


class ShellActionPatch(Strict):
    enabled: bool | None = None
    mode: ShellMode | None = None
    executable: str | None = None
    script_path: str | None = None
    args: list[str] | None = None
    stdin: Literal["event_json", "none"] | None = None
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)] | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> ShellActionPatch:
        if not self.model_fields_set:
            raise ValueError("at least one property is required")
        return self


class HttpActionPatch(Strict):
    enabled: bool | None = None
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    body: Any = None
    body_mode: Literal["template", "event_json", "none"] | None = None
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)] | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> HttpActionPatch:
        if not self.model_fields_set:
            raise ValueError("at least one property is required")
        return self


class PythonActionPatch(Strict):
    enabled: bool | None = None
    module: Annotated[str, Field(pattern=MODULE_PATTERN)] | None = None
    function: Annotated[str, Field(pattern=FUNCTION_PATTERN)] | None = None
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)] | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> PythonActionPatch:
        if not self.model_fields_set:
            raise ValueError("at least one property is required")
        return self


# ActionPatch is anyOf; callers select the concrete patch model by the existing action's type.
ActionPatch = ShellActionPatch | HttpActionPatch | PythonActionPatch


class ListActions(Strict):
    items: list[Action]
    next_cursor: str | None = None


# --------------------------------------------------------------------------------------
# Executions
# --------------------------------------------------------------------------------------

ExecutionStatus = Literal["queued", "running", "succeeded", "failed", "timed_out", "dropped"]


class Execution(Strict):
    id: str
    event_id: str | None = None
    rule_id: str | None = None
    action_id: str
    status: ExecutionStatus
    trigger: Literal["rule", "manual"]
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    exit_code: int | None = None
    http_status: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    result: Any = None
    error_code: str | None = None
    error_message: str | None = None
    action_revision: str | None = None


class ExecutionRequest(Strict):
    event: EventInput


class ListExecutions(Strict):
    items: list[Execution]
    next_cursor: str | None = None
    has_more: bool


# --------------------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------------------


class Problem(Strict):
    code: str
    message: str
    details: list[dict[str, Any]] | None = None
    request_id: str | None = None


class OpalErrorBody(Strict):
    """Nested error detail for :class:`OpalError` (contract v1.2 ``OpalError412``/``428``/``422``)."""

    code: str
    message: str
    details: Any = None
    request_id: str
    transaction_id: str
    session_id: str | None = None
    causation_id: str | None = None


class OpalError(Strict):
    """Suite-standard error envelope used for precondition (412/428) and OPAL-context header
    validation (422) failures introduced in opal_event.1.2.0.yaml. Unlike :class:`Problem`, the
    error payload is nested under an ``error`` key and always carries the request/transaction IDs."""

    error: OpalErrorBody


class Health(Strict):
    status: Literal["ok", "degraded"]
    broker_running: bool
    database_ready: bool
    pending_restart: bool


class HealthProbe(Strict):
    status: Literal["ok", "degraded", "unavailable"]
    details: dict[str, str] | None = None


class Stats(Strict):
    observed_events: Annotated[int, Field(ge=0)]
    invalid_events: Annotated[int, Field(ge=0)]
    matched_events: Annotated[int, Field(ge=0)]
    executions_succeeded: Annotated[int, Field(ge=0)]
    executions_failed: Annotated[int, Field(ge=0)]
    executions_dropped: Annotated[int, Field(ge=0)]
    queue_depth: Annotated[int, Field(ge=0)]
    active_workers: Annotated[int, Field(ge=0)]
    rejected_publications: Annotated[int, Field(ge=0)] = 0


class HTTPValidationErrorItem(Strict):
    loc: list[str | int]
    msg: str
    type: str


class HTTPValidationError(Strict):
    detail: list[HTTPValidationErrorItem]


# --------------------------------------------------------------------------------------
# Settings (full YAML-backed configuration)
# --------------------------------------------------------------------------------------


class ServerSettings(Strict):
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 8080


class CurveSettings(Strict):
    enabled: bool = False
    server_public_key_file: str | None = None
    server_secret_key_file: str | None = None
    authorized_client_keys_file: str | None = None


class ZeroMqSettings(Strict):
    publishers: str = "tcp://127.0.0.1:5555"
    subscribers: str = "tcp://127.0.0.1:5556"
    # Contract note: the OpenAPI schema marks this `const: 1048576`, but `PortableSettings.max_event_bytes`
    # (1..1048576) is explicitly PATCHable and documented to map onto this field. A rigid literal would make
    # that PATCH operation non-functional, so we treat 1048576 as the enforced ceiling, not a fixed value.
    max_event_bytes: Annotated[int, Field(ge=1, le=MAX_EVENT_BYTES)] = MAX_EVENT_BYTES
    curve: CurveSettings = Field(default_factory=CurveSettings)
    publisher_queue_size: Annotated[int, Field(ge=1, le=100000)] = 1000


class RestAuthSettings(Strict):
    enabled: bool = False
    token_env: str = "OPAL_EVENT_API_TOKEN"


class AllowedHttpDestination(Strict):
    host: Annotated[str, Field(min_length=1)]
    ports: list[Annotated[int, Field(ge=1, le=65535)]]
    schemes: list[Literal["http", "https"]]


class SecuritySettings(Strict):
    rest_auth: RestAuthSettings = Field(default_factory=RestAuthSettings)
    http_action_allowlist: list[AllowedHttpDestination] = Field(default_factory=list)


class ExecutionSettings(Strict):
    max_workers: Annotated[int, Field(ge=1, le=128)] = 4
    queue_size: Annotated[int, Field(ge=1, le=100000)] = 100
    default_timeout_seconds: Annotated[int, Field(ge=1, le=3600)] = 30
    overflow: Literal["drop_and_log"] = "drop_and_log"
    retry_enabled: Literal[False] = False
    max_output_bytes: Annotated[int, Field(ge=0, le=10485760)] = 65536


class PersistenceSettings(Strict):
    database: Annotated[str, Field(min_length=1)]
    event_mode: Literal["all", "matched", "none"] = "all"
    retention_days: Annotated[int, Field(ge=1, le=3650)] = 7
    execution_history_enabled: Literal[True] = True


class Settings(Strict):
    version: Literal[1] = 1
    server: ServerSettings = Field(default_factory=ServerSettings)
    zeromq: ZeroMqSettings = Field(default_factory=ZeroMqSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    persistence: PersistenceSettings
    actions: list[Action] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)


class PortablePersistenceSettings(Strict):
    event_mode: Literal["all", "matched", "none"] = "all"
    retention_days: Annotated[int, Field(ge=1, le=3650)] = 7
    execution_history_enabled: Literal[True] = True


class PortableSettings(Strict):
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    persistence: PortablePersistenceSettings = Field(default_factory=PortablePersistenceSettings)
    max_event_bytes: Annotated[int, Field(ge=1, le=1048576)] = MAX_EVENT_BYTES


class PortableSettingsPatch(Strict):
    execution: ExecutionSettings | None = None
    persistence: PortablePersistenceSettings | None = None
    max_event_bytes: Annotated[int, Field(ge=1, le=1048576)] | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> PortableSettingsPatch:
        if not self.model_fields_set:
            raise ValueError("at least one property is required")
        return self


# --------------------------------------------------------------------------------------
# Backup
# --------------------------------------------------------------------------------------


class ConfigBackup(Strict):
    format_version: Literal[1]
    settings: PortableSettings
    actions: list[Action]
    rules: list[Rule]


class ValidationReport(Strict):
    valid: bool
    errors: list[str]
    warnings: list[str]


class BackupImportRequest(Strict):
    backup: ConfigBackup


class BackupImportResult(Strict):
    revision: str
    pending_restart: bool
