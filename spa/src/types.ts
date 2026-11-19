export interface Problem {
  code: string;
  message: string;
  details?: Record<string, unknown>[];
  request_id?: string;
}

export interface EventItem {
  id: string;
  topic: string;
  source: string;
  timestamp: string;
  data: unknown;
}

// History endpoints return bounded arrays (newest first); `limit` caps the count.
export type ListEvents = EventItem[];

export interface Condition {
  path: string;
  operator: "eq" | "ne" | "gt" | "gte" | "lt" | "lte" | "in" | "not_in" | "exists" | "contains";
  value: unknown;
}

export interface Rule {
  id: string;
  enabled: boolean;
  topic: string;
  conditions: Condition[];
  actions: string[];
}

// Bounded configuration catalogs return the complete array (no pagination).
export type ListRules = Rule[];

export type Action =
  | {
      id: string;
      type: "shell";
      enabled: boolean;
      mode: "exec" | "sh" | "bash" | "cmd" | "powershell";
      executable?: string | null;
      script_path?: string | null;
      args: string[];
      stdin: "event_json" | "none";
      timeout_seconds?: number | null;
    }
  | {
      id: string;
      type: "http";
      enabled: boolean;
      method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
      url: string;
      headers?: Record<string, string> | null;
      body?: unknown;
      body_mode: "template" | "event_json" | "none";
      timeout_seconds?: number | null;
    }
  | {
      id: string;
      type: "python";
      enabled: boolean;
      module: string;
      function: string;
      timeout_seconds?: number | null;
    };

export type ListActions = Action[];

export interface Execution {
  id: string;
  event_id: string | null;
  rule_id: string | null;
  action_id: string;
  status: "queued" | "running" | "completed" | "failed" | "timed_out" | "dropped";
  trigger: "rule" | "manual";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  exit_code: number | null;
  http_status: number | null;
  stdout: string | null;
  stderr: string | null;
  result: unknown;
  error_code: string | null;
  error_message: string | null;
  action_revision?: string | null;
}

export type ListExecutions = Execution[];

export interface Health {
  name: string;
  version: string;
  api_version: string;
  status: "ok" | "degraded" | "error";
  live: boolean;
  ready: boolean;
  details: {
    broker_running?: boolean;
    database_ready?: boolean;
    executor_healthy?: boolean;
    stats?: Stats;
    [key: string]: unknown;
  };
  active_profile_id?: string | null;
  checked_at?: string;
  uptime_seconds?: number;
  pending_restart?: boolean | string[];
  pending_restart_fields?: string[];
}

export interface Stats {
  observed_events: number;
  invalid_events: number;
  matched_events: number;
  executions_completed: number;
  executions_failed: number;
  executions_dropped: number;
  queue_depth: number;
  active_workers: number;
  rejected_publications: number;
}

export interface ExecutionSettings {
  max_workers: number;
  queue_size: number;
  default_timeout_seconds: number;
  overflow: "drop_and_log";
  retry_enabled: false;
  max_output_bytes: number;
}

export interface PortablePersistenceSettings {
  event_mode: "all" | "matched" | "none";
  retention_days: number;
  execution_history_enabled: true;
}

// Kernel OpalSettingsMap (v0.1.0): flat map of registered setting keys to effective values.
export type SettingsMap = Record<string, unknown>;

export interface RuleTestResult {
  matched: boolean;
  action_ids: string[];
  checks: Record<string, unknown>[];
}

export interface Persona {
  persona_id: string;
  name: string;
  description?: string | null;
  voice_id?: string | null;
  enabled: boolean;
  // Kernel OpalPersonaRead provenance; "local" when authored here (opal-event is standalone).
  origin?: "local" | "owner";
  created_at: string;
  updated_at: string;
}

export interface PersonaCreate {
  persona_id: string;
  name: string;
  description?: string | null;
  voice_id?: string | null;
  enabled?: boolean;
}

export interface PersonaUpdate {
  name?: string;
  description?: string | null;
  voice_id?: string | null;
  enabled?: boolean;
}

// ---------------------------------------------------------------------------
// Profiles (v0.1.0 kernel v3): namespaced documents, shared + module compartments
// ---------------------------------------------------------------------------

export interface Profile {
  id: string;
  name: string;
  description?: string | null;
  is_active: boolean;
  shared: Record<string, unknown>;
  modules: Record<string, Record<string, unknown>>;
  created_at: string;
  updated_at: string;
}

export interface ProfileCreate {
  id?: string;
  name: string;
  description?: string | null;
  is_active?: boolean;
  shared?: Record<string, unknown>;
  modules?: Record<string, Record<string, unknown>>;
}

export interface ProfilePatch {
  name?: string;
  description?: string | null;
  is_active?: boolean;
  shared?: Record<string, unknown>;
  modules?: Record<string, Record<string, unknown>>;
}

// ---------------------------------------------------------------------------
// Service lifecycle (v0.1.0 kernel `service` tag)
// ---------------------------------------------------------------------------

export interface ServiceStatus {
  state: "running" | "stopped";
  pid?: number | null;
  previous_pid?: number | null;
  log_file?: string | null;
}

export interface ServiceLogTail {
  log_file: string;
  lines: string[];
}

// Configuration snapshots (v0.1.0 kernel `config` tag)
export interface ConfigSnapshot {
  format_version: string;
  module: string;
  created_at: string;
  settings: Record<string, unknown>;
  resources: Record<string, unknown[]>;
}

export interface ConfigSaveResult {
  snapshot: ConfigSnapshot;
  managed: boolean;
}

export interface ValidationIssue {
  code: string;
  path: string;
  message: string;
}

export interface ConfigValidation {
  valid: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

export interface ConfigLoadResult {
  applied: boolean;
  pending_restart_fields?: string[];
  warnings?: ValidationIssue[];
}

export interface ValidationReport {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export interface HealthProbe {
  status: "ok" | "degraded" | "unavailable";
  ready: boolean;
  details?: Record<string, string> | null;
}

export interface SettingRead {
  key: string;
  value: unknown;
  configured_value?: unknown;
  updated_at?: string | null;
  default?: unknown;
  has_override?: boolean;
  is_default: boolean;
  source?: "default" | "persisted" | "dotenv" | "environment";
  restart_required?: boolean;
  pending_restart?: boolean;
  warnings?: Record<string, unknown>[] | null;
}
