const baseUrl = import.meta.env.VITE_GATEWAY_URL || "http://127.0.0.1:8000";
const accessToken = () => sessionStorage.getItem("openkate.access_token") || import.meta.env.VITE_ACCESS_TOKEN;

export type RiskLevel = "low" | "medium" | "high" | "critical";
export type ScenarioStatus = "draft" | "in_review" | "approved" | "rejected" | "archived" | "deprecated";
export type EvidencePoint = { channel: "ui" | "api" | "state"; target: string; observation: string; assertions: { path: string; operator: string; expected?: unknown }[]; required: boolean };
export type Review = { id: string; author: string; content: string; status: "open" | "resolved"; createdAt: string };
export type Scenario = { id: string; projectId: string; title: string; businessGoal: string; actors: string[]; preconditions: string[]; riskLevel: RiskLevel; invariants: string[]; risks: { title: string; description: string; level: RiskLevel }[]; evidencePoints: EvidencePoint[]; tags: string[]; owner: string; status: ScenarioStatus; version: number; revision: number; reviews: Review[]; updatedAt: string };
export type ScenarioList = { items: Scenario[]; total: number; page: number; pageSize: number; degraded?: boolean };
export type ScenarioVersion = Omit<Scenario, "revision" | "reviews">;
export type Diff = { scenarioId: string; fromVersion: number; toVersion: number; changes: { field: string; from: unknown; to: unknown }[] };
export type Health = { status: string; services: { service: string; status: string }[] };
export type Environment = { id: string; name: string; base_url: string; write_policy: string; allowed_hosts: string[]; account_refs: string[]; data_set_refs: string[]; secret_refs: Record<string, string> };
export type CurrentUser = { id: string; name: string; email?: string; role: string; roles: string[] };
export type Workspace = { id: string; name: string };
export type Project = { id: string; workspaceId: string; name: string; description: string; archivedAt?: string };
export type Member = { userId: string; role: string };
export type AuditLog = { id: string; actor: string; action: string; occurredAt: string };
export type ExecutionStep = { id: string; channel: "ui" | "api" | "state"; action: string; dependsOn: string[]; input: Record<string, unknown>; save: Record<string, string>; timeoutMs: number; idempotent: boolean };
export type ExecutionPlan = { id: string; scenarioId: string; scenarioVersion: number; status: string; version: number; revision: number; steps: ExecutionStep[]; orderedStepIds: string[]; variables: Record<string, unknown>; timeoutMs: number };
export type StepResult = { stepId: string; status: "pending" | "running" | "completed" | "failed" | "canceled"; startedAt?: string; completedAt?: string; assertions: { passed?: boolean }[]; evidenceRefs: string[]; error?: { category: string; message: string } };
export type ExecutionRun = { id: string; planId: string; scenarioId: string; status: "running" | "completed" | "failed" | "canceled"; attempt: number; retryOf?: string; leaseId: string; variables: string[]; stepResults: StepResult[]; createdAt: string; completedAt?: string };
export type RunEvents = { events: { eventId: string; eventType: string; occurredAt: string; payload: Record<string, unknown> }[]; next: number };

type ApiResult<T> = { data: T; etag?: string; degraded: boolean };

async function request<T>(path: string, init?: RequestInit): Promise<ApiResult<T>> {
  const token = accessToken();
  const response = await fetch(`${baseUrl}${path}`, { ...init, headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(init?.headers || {}) } });
  const body = response.status === 204 ? null : await response.json();
  if (!response.ok) throw new Error(body?.error?.message || body?.detail || "Request failed");
  return { data: body as T, etag: response.headers.get("etag") || undefined, degraded: response.headers.get("x-openkate-read-model") === "degraded" };
}

const encode = (filters: Record<string, string | undefined>) => {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => value && params.set(key, value));
  return params.toString() ? `?${params}` : "";
};

export const api = {
  me: () => request<CurrentUser>("/api/v1/me"),
  workspaces: () => request<Workspace[]>("/api/v1/workspaces"),
  createWorkspace: (name: string) => request<Workspace>("/api/v1/workspaces", { method: "POST", body: JSON.stringify({ name }) }),
  projects: (workspaceId: string) => request<Project[]>(`/api/v1/workspaces/${workspaceId}/projects`),
  createProject: (workspaceId: string, payload: object) => request<Project>(`/api/v1/workspaces/${workspaceId}/projects`, { method: "POST", body: JSON.stringify(payload) }),
  updateProject: (projectId: string, payload: object) => request<Project>(`/api/v1/projects/${projectId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  archiveProject: (projectId: string) => request<Project>(`/api/v1/projects/${projectId}/archive`, { method: "POST" }),
  members: (projectId: string) => request<Member[]>(`/api/v1/projects/${projectId}/members`),
  createMember: (projectId: string, payload: object) => request<Member>(`/api/v1/projects/${projectId}/members`, { method: "POST", body: JSON.stringify(payload) }),
  updateMember: (projectId: string, userId: string, role: string) => request<Member>(`/api/v1/projects/${projectId}/members/${userId}`, { method: "PATCH", body: JSON.stringify({ role }) }),
  deleteMember: (projectId: string, userId: string) => request<null>(`/api/v1/projects/${projectId}/members/${userId}`, { method: "DELETE" }),
  auditLogs: (projectId: string) => request<AuditLog[]>(`/api/v1/projects/${projectId}/audit-logs`),
  health: () => request<Health>("/api/v1/system/health"),
  scenarios: (filters: Record<string, string | undefined>) => request<ScenarioList>(`/api/v1/projects/project_demo/scenarios${encode(filters)}`),
  scenario: (id: string) => request<Scenario>(`/api/v1/scenarios/${id}`),
  createScenario: (payload: object) => request<Scenario>("/api/v1/projects/project_demo/scenarios", { method: "POST", body: JSON.stringify(payload) }),
  updateScenario: (id: string, payload: object, etag: string) => request<Scenario>(`/api/v1/scenarios/${id}`, { method: "PATCH", headers: { "If-Match": etag }, body: JSON.stringify(payload) }),
  submitReview: (id: string, etag: string) => request<Scenario>(`/api/v1/scenarios/${id}/submit-review`, { method: "POST", headers: { "If-Match": etag } }),
  addReview: (id: string, content: string, etag: string) => request<Scenario>(`/api/v1/scenarios/${id}/reviews`, { method: "POST", headers: { "If-Match": etag }, body: JSON.stringify({ content }) }),
  resolveReview: (id: string, reviewId: string, etag: string) => request<Scenario>(`/api/v1/scenarios/${id}/reviews/${reviewId}`, { method: "PATCH", headers: { "If-Match": etag }, body: JSON.stringify({ status: "resolved" }) }),
  approve: (id: string, etag: string) => request<Scenario>(`/api/v1/scenarios/${id}/approve`, { method: "POST", headers: { "If-Match": etag } }),
  reject: (id: string, reason: string, etag: string) => request<Scenario>(`/api/v1/scenarios/${id}/reject`, { method: "POST", headers: { "If-Match": etag }, body: JSON.stringify({ reason }) }),
  archive: (id: string, etag: string) => request<Scenario>(`/api/v1/scenarios/${id}/archive`, { method: "POST", headers: { "If-Match": etag } }),
  deprecate: (id: string, etag: string) => request<Scenario>(`/api/v1/scenarios/${id}/deprecate`, { method: "POST", headers: { "If-Match": etag } }),
  versions: (id: string) => request<ScenarioVersion[]>(`/api/v1/scenarios/${id}/versions`),
  diff: (id: string, fromVersion: number, toVersion: number) => request<Diff>(`/api/v1/scenarios/${id}/diff?fromVersion=${fromVersion}&toVersion=${toVersion}`),
  environments: (projectId: string) => request<Environment[]>(`/api/v1/projects/${projectId}/environments`),
  createEnvironment: (projectId: string, payload: object) => request<Environment>(`/api/v1/projects/${projectId}/environments`, { method: "POST", body: JSON.stringify(payload) }),
  updateEnvironment: (projectId: string, environmentId: string, payload: object) => request<Environment>(`/api/v1/projects/${projectId}/environments/${environmentId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  createExecutionPlan: (scenarioId: string, payload: object) => request<ExecutionPlan>(`/api/v1/scenarios/${scenarioId}/execution-plans`, { method: "POST", body: JSON.stringify(payload) }),
  createRun: (scenarioId: string, payload: object) => request<ExecutionRun>(`/api/v1/scenarios/${scenarioId}/runs`, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify(payload) }),
  run: (runId: string) => request<ExecutionRun>(`/api/v1/runs/${runId}`),
  runEvents: (runId: string) => request<RunEvents>(`/api/v1/runs/${runId}/events`),
  cancelRun: (runId: string) => request<{ runId: string; status: string }>(`/api/v1/runs/${runId}/cancel`, { method: "POST" }),
  retryRun: (runId: string) => request<ExecutionRun>(`/api/v1/runs/${runId}/retry`, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() } }),
};
