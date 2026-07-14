const baseUrl = import.meta.env.VITE_GATEWAY_URL || "http://127.0.0.1:8000";
export type Project = { id: string; name: string; description: string; createdAt: string };
export type Health = { status: string; services: { service: string; status: string }[] };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, { ...init, headers: { "Content-Type": "application/json", "X-OpenKATE-Role": "owner", ...(init?.headers || {}) } });
  if (!response.ok) throw new Error((await response.json()).detail || "Request failed");
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/api/v1/system/health"),
  projects: () => request<Project[]>("/api/v1/workspaces/workspace_demo/projects"),
  createProject: (name: string, description: string) => request<Project>("/api/v1/workspaces/workspace_demo/projects", { method: "POST", body: JSON.stringify({ name, description }) })
};
