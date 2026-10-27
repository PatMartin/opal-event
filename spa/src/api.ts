import type { Problem } from "./types";

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

/** Thin wrapper exposing one typed call per contract operationId, mirroring the CLI's client. */
export const api = {
  publishEvent: (body: { topic: string; data: unknown; id?: string; source?: string; timestamp?: string }) =>
    request<{ id: string; status: string; published_at: string }>("POST", "/api/v1/events/", body),
  listEvents: (query: Record<string, string | number | undefined>) =>
    request<import("./types").ListEvents>("GET", withQuery("/api/v1/events/", query)),
  deleteEvent: (eventId: string) =>
    request<void>("DELETE", `/api/v1/events/${encodeURIComponent(eventId)}`),
  clearEvents: () => request<void>("DELETE", "/api/v1/events/"),
  listTopics: () => request<{ items: string[] }>("GET", "/api/v1/topics/"),

  // Rules/Actions are bounded configuration catalogs as of opal_event.1.2.0.yaml: GET returns the
  // complete collection (no cursor/limit) with a strong ETag, and PATCH/DELETE require a matching
  // If-Match (428 if omitted server-side, 412 if stale). get{Rule,Action} below fetch-then-mutate
  // to auto-populate If-Match, mirroring the CLI's `_resource_etag` helper.
  listRules: () => request<import("./types").ListRules>("GET", "/api/v1/rules/"),
  createRule: (body: import("./types").Rule) => request<import("./types").Rule>("POST", "/api/v1/rules/", body),
  getRule: (ruleId: string) => fetchWithEtag<import("./types").Rule>(`/api/v1/rules/${encodeURIComponent(ruleId)}`),
  deleteRule: async (ruleId: string, ifMatch?: string): Promise<void> => {
    const etag = ifMatch ?? (await api.getRule(ruleId)).etag;
    await request<void>("DELETE", `/api/v1/rules/${encodeURIComponent(ruleId)}`, undefined, { "If-Match": etag });
  },
  patchRule: async (ruleId: string, patch: Partial<import("./types").Rule>, ifMatch?: string) => {
    const etag = ifMatch ?? (await api.getRule(ruleId)).etag;
    return request<import("./types").Rule>("PATCH", `/api/v1/rules/${encodeURIComponent(ruleId)}`, patch, {
      "If-Match": etag,
    });
  },
  testRule: (ruleId: string, event: unknown) =>
    request<import("./types").RuleTestResult>("POST", `/api/v1/rules/${encodeURIComponent(ruleId)}/test`, { event }),

  listActions: () => request<import("./types").ListActions>("GET", "/api/v1/actions/"),
  createAction: (body: import("./types").Action) => request<import("./types").Action>("POST", "/api/v1/actions/", body),
  getAction: (actionId: string) =>
    fetchWithEtag<import("./types").Action>(`/api/v1/actions/${encodeURIComponent(actionId)}`),
  deleteAction: async (actionId: string, ifMatch?: string): Promise<void> => {
    const etag = ifMatch ?? (await api.getAction(actionId)).etag;
    await request<void>("DELETE", `/api/v1/actions/${encodeURIComponent(actionId)}`, undefined, { "If-Match": etag });
  },
  patchAction: async (actionId: string, patch: Record<string, unknown>, ifMatch?: string) => {
    const etag = ifMatch ?? (await api.getAction(actionId)).etag;
    return request<import("./types").Action>("PATCH", `/api/v1/actions/${encodeURIComponent(actionId)}`, patch, {
      "If-Match": etag,
    });
  },
  executeAction: (actionId: string, event: unknown) =>
    request<import("./types").Execution>("POST", `/api/v1/actions/${encodeURIComponent(actionId)}/execute`, { event }),

  listExecutions: (query: Record<string, string | number | undefined>) =>
    request<import("./types").ListExecutions>("GET", withQuery("/api/v1/executions/", query)),
  getExecution: (executionId: string) =>
    request<import("./types").Execution>("GET", `/api/v1/executions/${encodeURIComponent(executionId)}`),

  getHealth: () => request<import("./types").Health>("GET", "/api/v1/health/"),
  getStats: () => request<import("./types").Stats>("GET", "/api/v1/stats/"),

  getSettings: async (): Promise<{ settings: import("./types").PortableSettings; etag: string }> => {
    const resp = await fetch("/api/v1/settings/", { headers: { ...contextHeaders(), ...authHeaders() } });
    const data = await resp.json();
    if (!resp.ok) throw new ApiError(resp.status, data);
    return { settings: data, etag: resp.headers.get("etag") || "" };
  },
  patchSettings: (patch: Record<string, unknown>, ifMatch: string) =>
    request<import("./types").PortableSettings>("PATCH", "/api/v1/settings/", patch, { "If-Match": ifMatch }),

  exportBackup: () => request<import("./types").ConfigBackup>("GET", "/api/v1/backup/export"),
  validateBackup: (backup: unknown) =>
    request<import("./types").ValidationReport>("POST", "/api/v1/backup/validate", backup),
  importBackup: (backup: unknown, ifMatch: string) =>
    request<import("./types").BackupImportResult>("POST", "/api/v1/backup/import", { backup }, { "If-Match": ifMatch }),
};

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
