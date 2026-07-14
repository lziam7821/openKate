import { FormEvent, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, Health, Project } from "./api";
import "./styles.css";

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const refresh = async () => {
    try { setError(""); const [nextProjects, nextHealth] = await Promise.all([api.projects(), api.health()]); setProjects(nextProjects); setHealth(nextHealth); }
    catch (err) { setError(err instanceof Error ? err.message : "Gateway unavailable"); }
  };
  useEffect(() => { void refresh(); }, []);
  const submit = async (event: FormEvent) => { event.preventDefault(); if (!name.trim()) return; try { await api.createProject(name.trim(), "Created from Web Console"); setName(""); await refresh(); } catch (err) { setError(err instanceof Error ? err.message : "Create failed"); } };

  return <main><aside><div className="brand">OpenKATE</div><span>v0.1.0 · Foundation</span><nav><b>Workspace</b><a className="active">Projects</a><a>Environments</a><a>Members</a><a>Service health</a></nav></aside><section><header><div><p>OPENKATE WORKSPACE</p><h1>Projects</h1><span>Create the ownership boundary for validation work.</span></div><button onClick={() => void refresh()}>Refresh</button></header>{error && <div className="error">{error}</div>}<div className="grid"><article><h2>Create project</h2><form onSubmit={submit}><label>Project name<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Checkout validation" /></label><label>Description<input value="Created from Web Console" readOnly /></label><button type="submit">Create project</button></form></article><article><h2>Service health</h2>{health ? health.services.map((service) => <div className="service" key={service.service}><span className={service.status === "ready" ? "dot ready" : "dot"}></span>{service.service}<b>{service.status}</b></div>) : <p>Checking services...</p>}</article></div><article className="list"><h2>Projects <span>{projects.length}</span></h2>{projects.length ? projects.map((project) => <div className="project" key={project.id}><div><b>{project.name}</b><p>{project.description || "No description"}</p></div><code>{project.id}</code></div>) : <p>No projects yet. Create the first ownership boundary.</p>}</article></section></main>;
}

createRoot(document.getElementById("root")!).render(<App />);
