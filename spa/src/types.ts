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
  status: "queued" | "running" | "succeeded" | "failed" | "timed_out" | "dropped";
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
  status: "ok" | "degraded";
  broker_running: boolean;
  database_ready: boolean;
  pending_restart: boolean;
}

export interface Stats {
  observed_events: number;
  invalid_events: number;
  matched_events: number;
  executions_succeeded: number;
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

export interface PortableSettings {
  execution: ExecutionSettings;
  persistence: PortablePersistenceSettings;
  max_event_bytes: number;
}

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

export interface ServiceInfo {
  name: string;
  version: string;
  api_version: string;
  description?: string | null;
}

export interface LivenessRead {
  status: "ok";
}

// ---------------------------------------------------------------------------
// Profiles (v1.7.1): named presets of portable operating defaults
// ---------------------------------------------------------------------------

export interface PortableSettingsPatch {
  execution?: Partial<ExecutionSettings>;
  persistence?: Partial<PortablePersistenceSettings>;
  max_event_bytes?: number;
}

export interface Profile {
  id: number;
  name: string;
  description?: string | null;
  is_active: boolean;
  settings?: PortableSettingsPatch | null;
  created_at: string;
  updated_at: string;
}

export interface ProfileCreate {
  id?: number;
  name: string;
  description?: string | null;
  is_active?: boolean;
  settings?: PortableSettingsPatch | null;
}

export interface ProfilePatch {
  name?: string;
  description?: string | null;
  is_active?: boolean;
  settings?: PortableSettingsPatch | null;
}

// ---------------------------------------------------------------------------
// Managed backup archives + service lifecycle (v1.7.1)
// ---------------------------------------------------------------------------

export interface BackupInfo {
  filename: string;
  size_bytes: number;
  created_at: string;
}

export interface BackupCreateResponse {
  filename: string;
  message?: string | null;
}

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

export interface ConfigBackup {
  format_version: 1;
  exported_at?: string | null;
  settings: PortableSettings;
  actions: Action[];
  rules: Rule[];
  personas: Persona[];
  profiles: Profile[];
}

export interface ValidationReport {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export interface BackupImportResult {
  revision: string;
  pending_restart: boolean;
}

export interface HealthProbe {
  status: "ok" | "degraded" | "unavailable";
  ready: boolean;
  details?: Record<string, string> | null;
}

export interface SettingRead {
  key: string;
  value: unknown;
  default?: unknown;
  is_default: boolean;
}
