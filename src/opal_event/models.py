"""Pydantic models mirroring the schemas in ``openapi/opal_event_v0.1.0.yaml``.

Field names, types, constraints, and ``additionalProperties: false`` semantics are kept in lock-step with the
OpenAPI contract. The contract is authoritative; do not add fields here that are not present there.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

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
    # Rule-dispatch disposition at admission: True/False once routed, None for rows
    # persisted before this field existed.
    matched: bool | None = None


class PublishReceipt(Strict):
    id: str
    status: Literal["accepted"]
    published_at: str


EventList = list[Event]


TopicList = list[str]


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


RuleList = list[Rule]


class RuleTestInput(Strict):
    event: EventInput


class RuleTestResult(Strict):
    matched: bool
    action_ids: list[str]
    checks: list[dict[str, Any]]


# --------------------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------------------


def _unique_keywords(values: list[str]) -> list[str]:
    if len(set(values)) != len(values):
        raise ValueError("keywords must be unique")
    return values


_DESCRIPTION_FIELD = Annotated[str, Field(min_length=1, max_length=2048)]
_KEYWORDS_FIELD = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=64)]], Field(max_length=16), AfterValidator(_unique_keywords)
]
_SUGGESTED_FIELD = Annotated[list[Annotated[str, Field(min_length=1, max_length=512)]], Field(max_length=8)]
_MAX_INPUT_SCHEMA_BYTES = 16384


def _validate_declared_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Fail closed on malformed declared input_schema; reject remote $ref targets."""
    if schema is None:
        return None
    if not isinstance(schema, dict):
        raise ValueError("input_schema must be a JSON Schema object")
    text = json.dumps(schema)
    if len(text.encode()) > _MAX_INPUT_SCHEMA_BYTES:
        raise ValueError("input_schema exceeds 16 KiB")
    import jsonschema

    for match in re.finditer(r'"\$ref"\s*:\s*"([^"]+)"', text):
        if "://" in match.group(1) or not match.group(1).startswith("#"):
            raise ValueError("input_schema may only use local $ref targets")
    try:
        jsonschema.validators.validator_for(schema).check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise ValueError(f"input_schema is not a valid JSON Schema: {exc.message}") from exc
    return schema


def _action_metadata_fields() -> dict:
    return {
        "description": (_DESCRIPTION_FIELD | None, None),
        "input_schema": (dict[str, Any] | None, None),
        "keywords": (_KEYWORDS_FIELD | None, None),
        "suggested_prompts": (_SUGGESTED_FIELD | None, None),
    }


ShellMode = Literal["exec", "sh", "bash", "cmd", "powershell"]
_SCRIPT_MODES = {"sh", "bash", "cmd", "powershell"}


class ShellAction(Strict):
    id: str
    type: Literal["shell"]
    enabled: bool = True
    description: _DESCRIPTION_FIELD | None = None
    input_schema: dict[str, Any] | None = None
    keywords: _KEYWORDS_FIELD | None = None
    suggested_prompts: _SUGGESTED_FIELD | None = None

    @field_validator("input_schema")
    @classmethod
    def _check_declared_schema(cls, v):
        return _validate_declared_schema(v)

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
    description: _DESCRIPTION_FIELD | None = None
    input_schema: dict[str, Any] | None = None
    keywords: _KEYWORDS_FIELD | None = None
    suggested_prompts: _SUGGESTED_FIELD | None = None

    @field_validator("input_schema")
    @classmethod
    def _check_declared_schema(cls, v):
        return _validate_declared_schema(v)

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
    description: _DESCRIPTION_FIELD | None = None
    input_schema: dict[str, Any] | None = None
    keywords: _KEYWORDS_FIELD | None = None
    suggested_prompts: _SUGGESTED_FIELD | None = None

    @field_validator("input_schema")
    @classmethod
    def _check_declared_schema(cls, v):
        return _validate_declared_schema(v)

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

    description: _DESCRIPTION_FIELD | None = None
    input_schema: dict[str, Any] | None = None
    keywords: _KEYWORDS_FIELD | None = None
    suggested_prompts: _SUGGESTED_FIELD | None = None

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

    description: _DESCRIPTION_FIELD | None = None
    input_schema: dict[str, Any] | None = None
    keywords: _KEYWORDS_FIELD | None = None
    suggested_prompts: _SUGGESTED_FIELD | None = None

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

    description: _DESCRIPTION_FIELD | None = None
    input_schema: dict[str, Any] | None = None
    keywords: _KEYWORDS_FIELD | None = None
    suggested_prompts: _SUGGESTED_FIELD | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> PythonActionPatch:
        if not self.model_fields_set:
            raise ValueError("at least one property is required")
        return self


# ActionPatch is anyOf; callers select the concrete patch model by the existing action's type.
ActionPatch = ShellActionPatch | HttpActionPatch | PythonActionPatch


ActionList = list[Action]


# --------------------------------------------------------------------------------------
# Executions
# --------------------------------------------------------------------------------------

ExecutionStatus = Literal["queued", "running", "completed", "failed", "timed_out", "dropped"]


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
    # Scoped-principal that admitted this execution (None = admin, rule, or unscoped caller).
    owner: str | None = None


class ExecutionRequest(Strict):
    event: EventInput


ExecutionList = list[Execution]


# --------------------------------------------------------------------------------------
# Personas (v1.6.0): named automation identities, optionally bound to an OPAL Say voice
# --------------------------------------------------------------------------------------

PERSONA_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


def _utcnow() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class PersonaCreate(Strict):
    persona_id: Annotated[str, Field(pattern=PERSONA_ID_PATTERN, min_length=1, max_length=200)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=1000)] | None = None
    voice_id: Annotated[str, Field(min_length=1)] | None = None
    enabled: bool = True


class PersonaUpdate(Strict):
    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    description: Annotated[str, Field(max_length=1000)] | None = None
    voice_id: Annotated[str, Field(min_length=1)] | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> PersonaUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one property is required")
        return self


class PersonaRead(Strict):
    persona_id: str
    name: str
    description: str | None = None
    voice_id: str | None = None
    enabled: bool
    # Kernel ``OpalPersonaRead`` provenance: ``local`` (authored here) or ``owner`` (synced from
    # OPAL Think's authoritative registry). opal-event runs standalone — no owner_url — so every
    # record is ``local``; the field is additive and defaults applied on read.
    origin: Literal["local", "owner"] = "local"
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------------------


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


class HealthReport(Strict):
    """Kernel ``OpalHealthReport`` (v0.1.0): identity, liveness, readiness and diagnostics in one
    document. Replaces the retired ``GET /api/v1/info/`` and ``GET /api/v1/info/ping`` reports;
    module diagnostics live under ``details`` (``stats`` per ``x-opal-health-details``)."""

    name: str
    version: str
    api_version: str
    status: Literal["ok", "degraded", "error"]
    live: bool
    ready: bool
    details: dict[str, Any] = Field(default_factory=dict)
    active_profile_id: str | None = None
    checked_at: str
    uptime_seconds: float
    pending_restart: bool | list[str] = False
    pending_restart_fields: list[str] = Field(default_factory=list)


class SettingRead(Strict):
    """Kernel ``SettingRead`` (v0.1.0): one setting record with effective/configured/default
    values plus provenance metadata (``source``, ``has_override``, ``is_default``,
    ``restart_required``, ``pending_restart``, structured ``warnings``)."""

    key: str
    value: Any = None
    configured_value: Any = None
    updated_at: str | None = None
    default: Any = None
    has_override: bool = False
    is_default: bool = True
    source: Literal["default", "persisted", "dotenv", "environment"] = "default"
    restart_required: bool = False
    pending_restart: bool = False
    warnings: list[dict[str, Any]] | None = None


class SettingUpdate(Strict):
    """Replacement value for a single setting key (kernel ``SettingUpdate``)."""

    value: Any


class OpalHealthProbe(Strict):
    status: Literal["ok", "degraded", "unavailable"]
    ready: bool
    details: dict[str, str] | None = None


class Stats(Strict):
    observed_events: Annotated[int, Field(ge=0)]
    invalid_events: Annotated[int, Field(ge=0)]
    matched_events: Annotated[int, Field(ge=0)]
    executions_completed: Annotated[int, Field(ge=0)]
    executions_failed: Annotated[int, Field(ge=0)]
    executions_dropped: Annotated[int, Field(ge=0)]
    queue_depth: Annotated[int, Field(ge=0)]
    active_workers: Annotated[int, Field(ge=0)]
    rejected_publications: Annotated[int, Field(ge=0)] = 0
    chain_depth_rejections: Annotated[int, Field(ge=0)] = 0
    fanout_dropped: Annotated[int, Field(ge=0)] = 0


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
    # Contract note: the OpenAPI schema marks this `const: 1048576`, but the kernel settings
    # surface allows PUT /api/v1/setting/max_event_bytes (1..1048576) which maps onto this field.
    # A rigid literal would make that operation non-functional, so 1048576 is the ceiling.
    max_event_bytes: Annotated[int, Field(ge=1, le=MAX_EVENT_BYTES)] = MAX_EVENT_BYTES
    curve: CurveSettings = Field(default_factory=CurveSettings)
    publisher_queue_size: Annotated[int, Field(ge=1, le=100000)] = 1000


class ScopedPrincipal(Strict):
    """A named bearer credential with narrowed capabilities. The token itself lives in
    ``token_env`` (never in config). ``scope`` selects the capability set:

    - ``admin``   — full access (same surface as the main token).
    - ``publish`` — ``POST /api/v1/event/`` plus read-only info/health/topic routes; the
      event topic must start with one of ``topic_prefixes`` (empty list = deny all).
    - ``execute`` — the existing execute allowlist, narrowed further to the action ids
      in ``actions`` (``None`` = all actions, the legacy behavior). When
      ``own_receipts_only`` is set, execution reads are limited to receipts this
      principal admitted (``owner`` recorded at execute time).
    """

    name: Annotated[str, Field(min_length=1, max_length=128)]
    token_env: Annotated[str, Field(min_length=1)]
    scope: Literal["admin", "publish", "execute"] = "execute"
    topic_prefixes: list[Annotated[str, Field(max_length=256)]] = Field(default_factory=list)
    actions: list[Annotated[str, Field(max_length=128)]] | None = None
    own_receipts_only: bool = False


class RestAuthSettings(Strict):
    enabled: bool = False
    token_env: str = "OPAL_EVENT_API_TOKEN"
    # Optional second credential: callers holding this token may only list/show
    # actions, execute actions, and read executions — never administration.
    execute_token_env: str | None = None
    # Named principals with narrowed capabilities (e.g. producers restricted to topic
    # prefixes, or an executor like Think restricted to named actions + own receipts).
    principals: list[ScopedPrincipal] = Field(default_factory=list)


class AllowedHttpDestination(Strict):
    host: Annotated[str, Field(min_length=1)]
    ports: list[Annotated[int, Field(ge=1, le=65535)]]
    schemes: list[Literal["http", "https"]]


class SecuritySettings(Strict):
    rest_auth: RestAuthSettings = Field(default_factory=RestAuthSettings)
    http_action_allowlist: list[AllowedHttpDestination] = Field(default_factory=list)
    # Suite chain bound: a derived event/action declaring opal_chain_depth >= this limit is
    # refused admission (422 REST, skipped dispatch on the observer path). 16 = suite max.
    max_chain_depth: Annotated[int, Field(ge=0, le=1024)] = 16


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


class PortablePersistenceSettings(Strict):
    event_mode: Literal["all", "matched", "none"] = "all"
    retention_days: Annotated[int, Field(ge=1, le=3650)] = 7
    execution_history_enabled: Literal[True] = True


# --------------------------------------------------------------------------------------
# Profiles (v0.1.0 kernel v3): namespaced ``OpalProfile`` documents — ``shared`` plus a
# ``modules`` map of module-owned compartments. The opal-event compartment is an explicit
# empty closed schema (no profile-specific defaults); foreign compartments are stored and
# returned verbatim. ``is_active`` is a local selection, at most one per service.
# --------------------------------------------------------------------------------------

_PROFILE_COMPARTMENT = "opal-event"


def _check_profile_envelope(shared: dict | None, modules: dict | None, patch: bool = False) -> None:
    """Closed-envelope checks shared by create/patch payloads."""
    if shared:
        raise ValueError("'shared' is a closed empty object; members are rejected")
    for key, compartment in (modules or {}).items():
        if not isinstance(compartment, dict):
            raise ValueError(f"module compartment '{key}' must be an object")
        if patch and key != _PROFILE_COMPARTMENT:
            raise ValueError(f"foreign compartment '{key}' is read-only; patches may only target '{_PROFILE_COMPARTMENT}'")
        if key == _PROFILE_COMPARTMENT and compartment:
            raise ValueError(f"'modules.{_PROFILE_COMPARTMENT}' is a closed empty schema; members are rejected")


class ProfileCreate(Strict):
    """Kernel ``OpalProfileCreate``: ``name`` is required; omitted ``shared``/``modules``
    normalize to empty objects. Compartments for other modules are stored verbatim;
    ``is_active: true`` activates the new profile and deactivates all others."""

    id: Annotated[str, Field(min_length=1)] | None = None
    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(max_length=1000)] | None = None
    is_active: bool = False
    shared: dict[str, Any] = Field(default_factory=dict)
    modules: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _envelope(self) -> ProfileCreate:
        _check_profile_envelope(self.shared, self.modules)
        return self


class ProfilePatch(Strict):
    """Kernel ``OpalProfilePatch``: partial update; compartment values merge field-wise
    over the stored compartment. ``modules`` may contain only the receiving service's own
    compartment key — foreign compartment edits are rejected (422) and preserved."""

    name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    description: Annotated[str, Field(max_length=1000)] | None = None
    is_active: bool | None = None
    shared: dict[str, Any] | None = None
    modules: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> ProfilePatch:
        if not self.model_fields_set:
            raise ValueError("at least one property is required")
        _check_profile_envelope(self.shared, self.modules, patch=True)
        return self


class Profile(Strict):
    """Kernel ``OpalProfile``: opaque string ``id``, ``shared`` and ``modules`` required
    (empty objects valid). v1.7.1 stored profiles (integer ``id``, ``settings`` preset)
    upgrade on load: ids stringify, the dropped ``settings`` preset is discarded — the
    v3 opal-event compartment declares no fields to carry it."""

    id: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(max_length=1000)] | None = None
    is_active: bool
    shared: dict[str, Any] = Field(default_factory=dict)
    modules: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)

    @model_validator(mode="before")
    @classmethod
    def _migrate_v2(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            data.pop("settings", None)  # v2 preset; v3 opal-event compartment is empty
            data.setdefault("shared", {})
            data.setdefault("modules", {})
            if isinstance(data.get("id"), int):
                data["id"] = str(data["id"])
        return data


class Settings(Strict):
    version: Literal[1] = 1
    server: ServerSettings = Field(default_factory=ServerSettings)
    zeromq: ZeroMqSettings = Field(default_factory=ZeroMqSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    persistence: PersistenceSettings
    actions: list[Action] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    personas: list[PersonaRead] = Field(default_factory=list)
    profiles: list[Profile] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# Configuration snapshots (v0.1.0 kernel ``config`` tag): OpalConfigSnapshot envelope
# and the save/load/validate request/response shapes.
# --------------------------------------------------------------------------------------

SNAPSHOT_FORMAT_VERSION = "1.0.0"
SNAPSHOT_MODULE = "opal-event"
SNAPSHOT_RESOURCE_SECTIONS = ("profiles", "personas", "rules", "actions")


class ConfigSnapshot(Strict):
    """Kernel ``OpalConfigSnapshot``: closed envelope carrying flat setting-key overrides
    plus the module's declarative resource sections (profiles/personas/rules/actions).
    Persistent desired configuration only — never history, sessions, or secrets."""

    format_version: str = SNAPSHOT_FORMAT_VERSION
    module: str = SNAPSHOT_MODULE
    created_at: str = Field(default_factory=_utcnow)
    settings: dict[str, Any] = Field(default_factory=dict)
    resources: dict[str, Any] = Field(default_factory=dict)


class ConfigSaveRequest(Strict):
    destination: Literal["managed", "response"] = "managed"


class ConfigSaveResult(Strict):
    snapshot: ConfigSnapshot
    managed: bool


class ConfigLoadRequest(Strict):
    """Kernel ``OpalConfigLoadRequest``. ``snapshot`` omitted/``{}`` loads the managed
    snapshot; an explicit ``null`` is rejected in the handler (``model_fields_set``)."""

    snapshot: ConfigSnapshot | None = None


class ValidationIssue(Strict):
    """Kernel ``OpalValidationIssue``."""

    code: str
    path: str
    message: str


class ConfigLoadResult(Strict):
    applied: bool = True
    pending_restart_fields: list[str] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)


class ConfigValidation(Strict):
    """Kernel ``OpalConfigValidation``."""

    valid: bool
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)


class ValidationReport(Strict):
    valid: bool
    errors: list[str]
    warnings: list[str]


# --------------------------------------------------------------------------------------
# Service process lifecycle (v0.1.0 kernel ``service`` tag)
# --------------------------------------------------------------------------------------


class ServiceStartOptions(BaseModel):
    """Kernel ``OpalServiceStartOptions`` — ``additionalProperties: true``, so unknown fields are
    accepted and ignored rather than rejected."""

    model_config = ConfigDict(extra="allow")

    append: bool = False


class ServiceStatus(Strict):
    """Kernel ``OpalServiceStatus``: only ``state`` is required; the rest report the operation."""

    state: Literal["running", "stopped"]
    pid: int | None = None
    previous_pid: int | None = None
    log_file: str | None = None


class ServiceLogTail(Strict):
    """Kernel ``OpalServiceLogTail``: trailing log lines, oldest first."""

    log_file: str
    lines: list[str]
