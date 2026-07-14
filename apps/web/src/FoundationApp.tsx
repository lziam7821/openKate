import { FormEvent, useEffect, useState } from "react";
import { api, AuditLog, CurrentUser, Environment, Health, Member, Project, Workspace } from "./api";
import { auth } from "./auth";
import { parseCommaList } from "./foundation-utils";

export function FoundationApp() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [audits, setAudits] = useState<AuditLog[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const fail = (value: unknown) => setError(value instanceof Error ? value.message : "Request failed");
  const loadProjects = async (id: string) => { const result = await api.projects(id); setProjects(result.data); };
  const openProject = async (selected: Project) => {
    setProject(selected); setError("");
    try {
      const [nextEnvironments, nextMembers, nextAudits] = await Promise.all([api.environments(selected.id), api.members(selected.id), api.auditLogs(selected.id)]);
      setEnvironments(nextEnvironments.data); setMembers(nextMembers.data); setAudits(nextAudits.data);
    } catch (value) { fail(value); }
  };

  useEffect(() => {
    const load = async () => {
      try {
        if (window.location.pathname === "/signin-callback") { await auth.callback(); return; }
        if (!auth.token()) return;
        const [identity, nextWorkspaces, nextHealth] = await Promise.all([api.me(), api.workspaces(), api.health()]);
        setUser(identity.data); setWorkspaces(nextWorkspaces.data); setHealth(nextHealth.data);
        if (nextWorkspaces.data[0]) { setWorkspaceId(nextWorkspaces.data[0].id); await loadProjects(nextWorkspaces.data[0].id); }
      } catch (value) { fail(value); }
      finally { setLoading(false); }
    };
    void load();
  }, []);

  const createWorkspace = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    try { const result = await api.createWorkspace(String(form.get("name"))); setWorkspaces([...workspaces, result.data]); setWorkspaceId(result.data.id); setProjects([]); event.currentTarget.reset(); } catch (value) { fail(value); }
  };
  const createProject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    try { const result = await api.createProject(workspaceId, { name: form.get("name"), description: form.get("description") }); await loadProjects(workspaceId); await openProject(result.data); event.currentTarget.reset(); } catch (value) { fail(value); }
  };
  const saveProject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); if (!project) return; const form = new FormData(event.currentTarget);
    try { const result = await api.updateProject(project.id, { name: form.get("name"), description: form.get("description") }); setProject(result.data); await loadProjects(workspaceId); } catch (value) { fail(value); }
  };
  const saveEnvironment = async (event: FormEvent<HTMLFormElement>, environment?: Environment) => {
    event.preventDefault(); if (!project) return; const form = new FormData(event.currentTarget);
    const payload = { name: form.get("name"), base_url: form.get("baseUrl"), write_policy: form.get("writePolicy"), allowed_hosts: parseCommaList(form.get("allowedHosts")), account_refs: parseCommaList(form.get("accountRefs")), data_set_refs: parseCommaList(form.get("dataSetRefs")), secret_refs: {} };
    try { if (environment) await api.updateEnvironment(project.id, environment.id, payload); else await api.createEnvironment(project.id, payload); setEnvironments((await api.environments(project.id)).data); if (!environment) event.currentTarget.reset(); } catch (value) { fail(value); }
  };
  const addMember = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); if (!project) return; const form = new FormData(event.currentTarget);
    try { await api.createMember(project.id, { user_id: form.get("userId"), role: form.get("role") }); setMembers((await api.members(project.id)).data); event.currentTarget.reset(); } catch (value) { fail(value); }
  };

  if (loading) return <div className="auth-page"><article><h1>OpenKATE</h1><p>Loading your workspace…</p></article></div>;
  if (!user) return <div className="auth-page"><article><h1>OpenKATE</h1><p>Sign in to manage workspaces, projects, environments, and members.</p>{error && <div className="error">{error}</div>}<button disabled={!auth.configured} onClick={() => void auth.login().catch(fail)}>Sign in with OIDC</button>{!auth.configured && <small>Configure VITE_OIDC_AUTHORITY and VITE_OIDC_CLIENT_ID.</small>}</article></div>;

  return <main><aside><div className="brand">OpenKATE</div><span>{user.name} · {user.role}</span><nav><b>Foundation</b><a className="active">Projects</a><a href="/">Validation</a><b>System</b><a>Service health</a></nav><button className="secondary logout" onClick={auth.logout}>Sign out</button></aside><section><header><div><p>FOUNDATION / V0.1.0</p><h1>Workspace administration</h1><span>Create projects, secure environments, manage members, and review audit history.</span></div><div className="health-chip">{health?.status || "unknown"}</div></header>{error && <div className="error">{error}</div>}<div className="toolbar"><select value={workspaceId} onChange={(event) => { setWorkspaceId(event.target.value); setProject(null); void loadProjects(event.target.value).catch(fail); }}>{workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><form className="inline-form" onSubmit={createWorkspace}><input name="name" required placeholder="New workspace" /><button>Create</button></form></div><div className="foundation-grid"><div className="column"><article><h2>Create project</h2><form onSubmit={createProject}><label>Name<input name="name" required /></label><label>Description<textarea name="description" /></label><button disabled={!workspaceId}>Create project</button></form></article><article className="list"><h2>Projects <span>{projects.length}</span></h2>{projects.map((item) => <button key={item.id} className={`scenario ${project?.id === item.id ? "selected" : ""}`} onClick={() => void openProject(item)}><b>{item.name}</b><p>{item.description || "No description"}</p><small>{item.archivedAt ? "Archived" : "Active"}</small></button>)}</article></div><div className="detail">{project ? <><article><div className="detail-head"><div><h2>{project.name}</h2><p>{project.archivedAt ? "Archived project" : "Active project"}</p></div>{!project.archivedAt && <button className="danger" onClick={() => void api.archiveProject(project.id).then((result) => { setProject(result.data); void loadProjects(workspaceId); }).catch(fail)}>Archive</button>}</div><form onSubmit={saveProject}><label>Name<input name="name" defaultValue={project.name} /></label><label>Description<textarea name="description" defaultValue={project.description} /></label><button>Save project</button></form></article><article><h2>Environments</h2>{environments.map((item) => <form className="managed-row" key={item.id} onSubmit={(event) => void saveEnvironment(event, item)}><input name="name" defaultValue={item.name} aria-label="Environment name" /><input name="baseUrl" defaultValue={item.base_url} aria-label="Base URL" /><select name="writePolicy" defaultValue={item.write_policy}><option value="deny">deny</option><option value="read_only">read_only</option><option value="approval_required">approval_required</option></select><input name="allowedHosts" defaultValue={item.allowed_hosts.join(", ")} aria-label="Allowed hosts" /><input name="accountRefs" defaultValue={item.account_refs.join(", ")} hidden /><input name="dataSetRefs" defaultValue={item.data_set_refs.join(", ")} hidden /><button>Save</button></form>)}<form onSubmit={(event) => void saveEnvironment(event)}><div className="pair"><label>Name<input name="name" required /></label><label>Base URL<input name="baseUrl" required /></label></div><div className="pair"><label>Write policy<select name="writePolicy"><option value="deny">deny</option><option value="read_only">read_only</option><option value="approval_required">approval_required</option></select></label><label>Allowed hosts<input name="allowedHosts" /></label></div><input name="accountRefs" hidden /><input name="dataSetRefs" hidden /><button className="secondary">Add environment</button></form></article><article><h2>Members</h2>{members.map((item) => <div className="managed-row" key={item.userId}><b>{item.userId}</b><select value={item.role} onChange={(event) => void api.updateMember(project.id, item.userId, event.target.value).then(() => api.members(project.id)).then((result) => setMembers(result.data)).catch(fail)}>{["owner", "maintainer", "reviewer", "developer", "viewer"].map((role) => <option key={role}>{role}</option>)}</select><button className="danger" onClick={() => void api.deleteMember(project.id, item.userId).then(() => api.members(project.id)).then((result) => setMembers(result.data)).catch(fail)}>Remove</button></div>)}<form className="inline-form" onSubmit={addMember}><input name="userId" required placeholder="User ID" /><select name="role"><option>viewer</option><option>developer</option><option>reviewer</option><option>maintainer</option></select><button>Add member</button></form></article><article><h2>Audit log</h2>{audits.slice(0, 20).map((item) => <div className="audit-row" key={item.id}><b>{item.action}</b><span>{item.actor}</span><small>{new Date(item.occurredAt).toLocaleString()}</small></div>)}</article></> : <article className="empty"><h2>Select a project</h2><p>Choose a project to manage environments, members, and audit history.</p></article>}</div></div></section></main>;
}
