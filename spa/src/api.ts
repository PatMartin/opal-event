import type { Problem } from "./types";
import opsManifest from "./api.operations.json";

const TOKEN_STORAGE_KEY = "opal_event_token";

export function getToken(): string {
  return localStorage.getItem(TOKEN_STORAGE_KEY) || "";
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

const CONTEXT_STORAGE_KEY = "opal_event_context_ids";

export interface OpalContextIds {
  sessionId?: string;
  transactionId?: string;
  causationId?: string;
}

export function getContextIds(): OpalContextIds {
  try {
    return JSON.parse(localStorage.getItem(CONTEXT_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

export function setContextIds(ids: OpalContextIds): void {
  localStorage.setItem(CONTEXT_STORAGE_KEY, JSON.stringify(ids));
}

function contextHeaders(): Record<string, string> {
  const ids = getContextIds();
  const headers: Record<string, string> = {};
  if (ids.sessionId) headers["X-Opal-Session-Id"] = ids.sessionId;
  if (ids.transactionId) headers["X-Opal-Transaction-Id"] = ids.transactionId;
  if (ids.causationId) headers["X-Opal-Causation-Id"] = ids.causationId;
  return headers;
}

interface OpalErrorPayload {
  error: {
    code: string;
    message: string;
    details?: unknown;
    request_id: string;
    transaction_id: string;
    session_id: string | null;
    causation_id: string | null;
  };
}

export class ApiError extends Error {
  status: number;
  problem: Problem | { detail: unknown } | OpalErrorPayload | null;

  constructor(status: number, problem: Problem | { detail: unknown } | OpalErrorPayload | null) {
    super(describeProblem(status, problem));
    this.status = status;
    this.problem = problem;
  }
}

function describeProblem(status: number, problem: Problem | { detail: unknown } | OpalErrorPayload | null): string {
  if (problem && "error" in problem) return `HTTP ${status} ${problem.error.code}: ${problem.error.message}`;
  if (problem && "code" in problem) return `HTTP ${status} ${problem.code}: ${problem.message}`;
  if (problem && "detail" in problem) return `HTTP ${status} validation error: ${JSON.stringify(problem.detail)}`;
  return `HTTP ${status}`;
}

async function request<T>(method: string, path: string, body?: unknown, extraHeaders?: Record<string, string>): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...contextHeaders(), ...extraHeaders };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (resp.status === 204) return undefined as T;
  const text = await resp.text();
  const data = text ? JSON.parse(text) : undefined;
  if (!resp.ok) throw new ApiError(resp.status, data ?? null);
  return data as T;
}

async function fetchWithEtag<T>(path: string): Promise<{ data: T; etag: string }> {
  const resp = await fetch(path, { headers: { ...contextHeaders(), ...authHeaders() } });
  const data = await resp.json();
  if (!resp.ok) throw new ApiError(resp.status, data ?? null);
  return { data, etag: resp.headers.get("etag") || "" };
}

type OpId = keyof typeof opsManifest;
type ClientKey = (typeof opsManifest)[OpId]["client"];

function opMethod(op: OpId): string {
  return opsManifest[op].method;
}

function opPath(op: OpId, params?: Record<string, string>): string {
  let path: string = opsManifest[op].path;
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      path = path.replace(`{${key}}`, encodeURIComponent(value));
    }
  }
  return path;
}

/** Thin wrapper exposing one typed call per contract operationId, mirroring the CLI's client.
 * Methods/paths come from api.operations.json — the same manifest the contract tests verify
 * against openapi/opal_event_v1.6.0.yaml. `satisfies` makes tsc fail if a manifest client fn is missing. */
export const api = {
  publishEvent: (body: { topic: string; data: unknown; id?: string; source?: string; timestamp?: string }) =>
    request<{ id: string; status: string; published_at: string }>(
      opMethod("post_api_v1_events"),
      opPath("post_api_v1_events"),
      body,
    ),
  listEvents: (query: Record<string, string | number | undefined>) =>
    request<import("./types").ListEvents>(opMethod("get_api_v1_events"), withQuery(opPath("get_api_v1_events"), query)),
  deleteEvent: (eventId: string) =>
    request<void>(opMethod("delete_api_v1_events_id"), opPath("delete_api_v1_events_id", { event_id: eventId })),
  clearEvents: () => request<void>(opMethod("delete_api_v1_events"), opPath("delete_api_v1_events")),
  getEvent: (eventId: string) =>
    request<import("./types").EventItem>(opMethod("get_api_v1_events_id"), opPath("get_api_v1_events_id", { event_id: eventId })),
  listTopics: () => request<{ items: string[] }>(opMethod("get_api_v1_topics"), opPath("get_api_v1_topics")),

  // Rules/Actions are bounded configuration catalogs as of opal_event.1.2.0.yaml: GET returns the
  // complete collection (no cursor/limit) with a strong ETag, and PATCH/DELETE require a matching
  // If-Match (428 if omitted server-side, 412 if stale). get{Rule,Action} below fetch-then-mutate
  // to auto-populate If-Match, mirroring the CLI's `_resource_etag` helper.
  listRules: () => request<import("./types").ListRules>(opMethod("get_api_v1_rules"), opPath("get_api_v1_rules")),
  createRule: async (body: import("./types").Rule) => {
    const { etag } = await fetchWithEtag<import("./types").ListRules>(opPath("get_api_v1_rules"));
    return request<import("./types").Rule>(opMethod("post_api_v1_rules"), opPath("post_api_v1_rules"), body, {
      "If-Match": etag,
    });
  },
  getRule: (ruleId: string) =>
    fetchWithEtag<import("./types").Rule>(opPath("get_api_v1_rules_id", { rule_id: ruleId })),
  deleteRule: async (ruleId: string, ifMatch?: string): Promise<void> => {
    const etag = ifMatch ?? (await api.getRule(ruleId)).etag;
    await request<void>(opMethod("delete_api_v1_rules_id"), opPath("delete_api_v1_rules_id", { rule_id: ruleId }), undefined, {
      "If-Match": etag,
    });
  },
  patchRule: async (ruleId: string, patch: Partial<import("./types").Rule>, ifMatch?: string) => {
    const etag = ifMatch ?? (await api.getRule(ruleId)).etag;
    return request<import("./types").Rule>(
      opMethod("patch_api_v1_rules_rule_id"),
      opPath("patch_api_v1_rules_rule_id", { rule_id: ruleId }),
      patch,
      { "If-Match": etag },
    );
  },
  testRule: (ruleId: string, event: unknown) =>
    request<import("./types").RuleTestResult>(opMethod("post_api_v1_rules_id_test"), opPath("post_api_v1_rules_id_test", { rule_id: ruleId }), { event }),

  listActions: () => request<import("./types").ListActions>(opMethod("get_api_v1_actions"), opPath("get_api_v1_actions")),
  createAction: async (body: import("./types").Action) => {
    const { etag } = await fetchWithEtag<import("./types").ListActions>(opPath("get_api_v1_actions"));
    return request<import("./types").Action>(opMethod("post_api_v1_actions"), opPath("post_api_v1_actions"), body, {
      "If-Match": etag,
    });
  },
  getAction: (actionId: string) =>
    fetchWithEtag<import("./types").Action>(opPath("get_api_v1_actions_id", { action_id: actionId })),
  deleteAction: async (actionId: string, ifMatch?: string): Promise<void> => {
    const etag = ifMatch ?? (await api.getAction(actionId)).etag;
    await request<void>(opMethod("delete_api_v1_actions_id"), opPath("delete_api_v1_actions_id", { action_id: actionId }), undefined, {
      "If-Match": etag,
    });
  },
  patchAction: async (actionId: string, patch: Record<string, unknown>, ifMatch?: string) => {
    const etag = ifMatch ?? (await api.getAction(actionId)).etag;
    return request<import("./types").Action>(
      opMethod("patch_api_v1_actions_action_id"),
      opPath("patch_api_v1_actions_action_id", { action_id: actionId }),
      patch,
      { "If-Match": etag },
    );
  },
  executeAction: (actionId: string, event: unknown) =>
    request<import("./types").Execution>(
      opMethod("post_api_v1_actions_id_execute"),
      opPath("post_api_v1_actions_id_execute", { action_id: actionId }),
      { event },
    ),

  listExecutions: (query: Record<string, string | number | undefined>) =>
    request<import("./types").ListExecutions>(opMethod("get_api_v1_executions"), withQuery(opPath("get_api_v1_executions"), query)),
  getExecution: (executionId: string) =>
    request<import("./types").Execution>(
      opMethod("get_api_v1_executions_id"),
      opPath("get_api_v1_executions_id", { execution_id: executionId }),
    ),

  getHealth: () => request<import("./types").Health>(opMethod("opal_health_get"), opPath("opal_health_get")),
  getHealthLive: () => request<import("./types").HealthProbe>(opMethod("opal_health_live_get"), opPath("opal_health_live_get")),
  getHealthReady: () =>
    request<import("./types").HealthProbe>(opMethod("opal_health_ready_get"), opPath("opal_health_ready_get")),
  getStats: () => request<import("./types").Stats>(opMethod("get_api_v1_stats"), opPath("get_api_v1_stats")),

  getSettings: async (): Promise<{ settings: import("./types").PortableSettings; etag: string }> => {
    const resp = await fetch(opPath("opal_setting_list"), { headers: { ...contextHeaders(), ...authHeaders() } });
    const data = await resp.json();
    if (!resp.ok) throw new ApiError(resp.status, data);
    return { settings: data, etag: resp.headers.get("etag") || "" };
  },
  patchSettings: (patch: Record<string, unknown>, ifMatch: string) =>
    request<import("./types").PortableSettings>(opMethod("opal_setting_patch"), opPath("opal_setting_patch"), patch, {
      "If-Match": ifMatch,
    }),
  getSetting: (key: string) =>
    request<import("./types").SettingRead>(opMethod("opal_setting_get"), opPath("opal_setting_get", { key })),
  putSetting: (key: string, value: unknown, ifMatch: string) =>
    request<import("./types").SettingRead>(opMethod("opal_setting_put"), opPath("opal_setting_put", { key }), { value }, {
      "If-Match": ifMatch,
    }),
  deleteSetting: (key: string, ifMatch: string) =>
    request<void>(opMethod("opal_setting_delete"), opPath("opal_setting_delete", { key }), undefined, {
      "If-Match": ifMatch,
    }),

  exportBackup: () => request<import("./types").ConfigBackup>(opMethod("opal_backup_export"), opPath("opal_backup_export")),
  validateBackup: (backup: unknown) =>
    request<import("./types").ValidationReport>(opMethod("opal_backup_validate_post"), opPath("opal_backup_validate_post"), backup),
  importBackup: (backup: unknown, ifMatch: string) =>
    request<import("./types").BackupImportResult>(opMethod("opal_backup_import"), opPath("opal_backup_import"), { backup }, {
      "If-Match": ifMatch,
    }),

  getInfo: () => request<import("./types").ServiceInfo>(opMethod("opal_info_get"), opPath("opal_info_get")),
  pingInfo: () => request<import("./types").LivenessRead>(opMethod("opal_info_ping"), opPath("opal_info_ping")),

  // Personas are a bounded configuration catalog (v1.6.0): list returns the complete
  // collection with a strong ETag, and every mutation requires If-Match (428/412).
  listPersonas: (enabled?: boolean) =>
    request<import("./types").Persona[]>(
      opMethod("opal_persona_list"),
      withQuery(opPath("opal_persona_list"), { enabled: enabled === undefined ? undefined : String(enabled) }),
    ),
  createPersona: async (body: import("./types").PersonaCreate) => {
    const { etag } = await fetchWithEtag<import("./types").Persona[]>(opPath("opal_persona_list"));
    return request<import("./types").Persona>(opMethod("opal_persona_create"), opPath("opal_persona_create"), body, {
      "If-Match": etag,
    });
  },
  getPersona: (personaId: string) =>
    fetchWithEtag<import("./types").Persona>(opPath("opal_persona_get", { persona_id: personaId })),
  patchPersona: async (personaId: string, patch: import("./types").PersonaUpdate, ifMatch?: string) => {
    const etag = ifMatch ?? (await api.getPersona(personaId)).etag;
    return request<import("./types").Persona>(
      opMethod("opal_persona_patch"),
      opPath("opal_persona_patch", { persona_id: personaId }),
      patch,
      { "If-Match": etag },
    );
  },
  deletePersona: async (personaId: string, ifMatch?: string): Promise<void> => {
    const etag = ifMatch ?? (await api.getPersona(personaId)).etag;
    await request<void>(
      opMethod("opal_persona_delete"),
      opPath("opal_persona_delete", { persona_id: personaId }),
      undefined,
      { "If-Match": etag },
    );
  },
  validatePersona: (personaId: string) =>
    request<import("./types").ValidationReport>(
      opMethod("opal_persona_validate"),
      opPath("opal_persona_validate", { persona_id: personaId }),
    ),
} satisfies Record<ClientKey, unknown>;

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function withQuery(path: string, query: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}
