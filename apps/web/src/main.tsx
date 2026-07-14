import { FormEvent, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, Diff, EvidencePoint, Health, RiskLevel, Scenario, ScenarioStatus, ScenarioVersion } from "./api";
import { ExecutionPanel } from "./ExecutionPanel";
import { FoundationApp } from "./FoundationApp";
import "./styles.css";

const split = (value: string) => value.split("\n").map((item) => item.trim()).filter(Boolean);
const join = (value: string[]) => value.join("\n");

function App() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selected, setSelected] = useState<Scenario | null>(null);
  const [etag, setEtag] = useState("");
  const [health, setHealth] = useState<Health | null>(null);
  const [versions, setVersions] = useState<ScenarioVersion[]>([]);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [risk, setRisk] = useState("");
  const [degraded, setDegraded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [review, setReview] = useState("");

  const refresh = async () => {
    try {
      setError("");
      const [list, nextHealth] = await Promise.all([api.scenarios({ q: query || undefined, status: status || undefined, risk: risk || undefined }), api.health()]);
      setScenarios(list.data.items); setDegraded(list.degraded); setHealth(nextHealth.data);
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to load validation scenarios"); }
  };

  const openScenario = async (id: string) => {
    try {
      setError("");
      const [detail, history] = await Promise.all([api.scenario(id), api.versions(id)]);
      setSelected(detail.data); setEtag(detail.etag || ""); setVersions(history.data); setDiff(null); setReview("");
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to open scenario"); }
  };

  useEffect(() => { void refresh(); }, []);

  const afterWrite = async (scenario: Scenario, nextEtag?: string) => {
    setSelected(scenario); setEtag(nextEtag || ""); setNotice("Saved"); await refresh(); await openScenario(scenario.id);
  };

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const evidenceTarget = String(form.get("evidenceTarget") || "").trim();
    const evidenceObservation = String(form.get("evidenceObservation") || "").trim();
    const evidencePoints: EvidencePoint[] = evidenceTarget && evidenceObservation ? [{ channel: "state", target: evidenceTarget, observation: evidenceObservation, assertions: [{ path: "status", operator: "equals", expected: "expected" }], required: true }] : [];
    try {
      const result = await api.createScenario({ title: form.get("title"), businessGoal: form.get("businessGoal"), actors: String(form.get("actors")).split(",").map((item) => item.trim()).filter(Boolean), riskLevel: form.get("riskLevel"), tags: String(form.get("tags")).split(",").map((item) => item.trim()).filter(Boolean), preconditions: split(String(form.get("preconditions") || "")), invariants: split(String(form.get("invariants") || "")), evidencePoints });
      event.currentTarget.reset(); await afterWrite(result.data, result.etag); setNotice("Scenario created as draft");
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to create scenario"); }
  };

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); if (!selected) return;
    const form = new FormData(event.currentTarget);
    try {
      const result = await api.updateScenario(selected.id, { title: form.get("title"), businessGoal: form.get("businessGoal"), preconditions: split(String(form.get("preconditions") || "")), invariants: split(String(form.get("invariants") || "")), tags: String(form.get("tags")).split(",").map((item) => item.trim()).filter(Boolean) }, etag);
      await afterWrite(result.data, result.etag);
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to save scenario"); }
  };

  const transition = async (action: "submit" | "approve" | "reject") => {
    if (!selected) return;
    try {
      const result = action === "submit" ? await api.submitReview(selected.id, etag) : action === "approve" ? await api.approve(selected.id, etag) : await api.reject(selected.id, review || "Changes requested", etag);
      setReview(""); await afterWrite(result.data, result.etag); setNotice(action === "submit" ? "Submitted for review" : action === "approve" ? "Scenario approved" : "Scenario rejected");
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to update scenario"); }
  };

  const comment = async () => {
    if (!selected || !review.trim()) return;
    try { const result = await api.addReview(selected.id, review.trim(), etag); setReview(""); await afterWrite(result.data, result.etag); setNotice("Review comment added"); }
    catch (err) { setError(err instanceof Error ? err.message : "Unable to add review"); }
  };

  const compare = async () => {
    if (!selected || versions.length < 2) return;
    try { const result = await api.diff(selected.id, versions[versions.length - 2].version, versions[versions.length - 1].version); setDiff(result.data); }
    catch (err) { setError(err instanceof Error ? err.message : "Unable to load version diff"); }
  };

  return <main><aside><div className="brand">OpenKATE</div><span>v0.3.0 · Execution Fabric</span><nav><b>Validation</b><a className="active">Scenarios</a><a>Evidence plan</a><a>Reviews</a><b>Execution</b><a>Plans</a><a>Runs</a><b>System</b><a>Service health</a></nav></aside><section><header><div><p>PROJECT_DEMO / EXECUTION</p><h1>Business validation</h1><span>Version scenarios, approve evidence, then execute UI → API → State.</span></div><button onClick={() => void refresh()}>Refresh</button></header>{error && <div className="error">{error}</div>}{notice && <div className="notice">{notice}</div>}{degraded && <div className="warning">Scenario list is temporarily served from the validation service while the reporting read model catches up.</div>}<div className="toolbar"><input aria-label="Search scenarios" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search title or business goal" /><select aria-label="Filter status" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option>{(["draft", "in_review", "approved", "rejected"] as ScenarioStatus[]).map((item) => <option key={item}>{item}</option>)}</select><select aria-label="Filter risk" value={risk} onChange={(event) => setRisk(event.target.value)}><option value="">All risks</option>{(["low", "medium", "high", "critical"] as RiskLevel[]).map((item) => <option key={item}>{item}</option>)}</select><button onClick={() => void refresh()}>Apply</button></div><div className="workspace"><div className="column"><article><h2>Create scenario</h2><form onSubmit={create}><label>Title<input name="title" required placeholder="Checkout paid order" /></label><label>Business goal<textarea name="businessGoal" required placeholder="A paid checkout creates a paid order" /></label><div className="pair"><label>Actors<input name="actors" required placeholder="buyer, payment service" /></label><label>Risk<select name="riskLevel" defaultValue="medium">{(["low", "medium", "high", "critical"] as RiskLevel[]).map((item) => <option key={item}>{item}</option>)}</select></label></div><label>Tags<input name="tags" placeholder="checkout, payment" /></label><label>Preconditions <small>one per line</small><textarea name="preconditions" /></label><label>Business invariants <small>one per line</small><textarea name="invariants" /></label><label>Evidence target<input name="evidenceTarget" placeholder="postgresql.orders" /></label><label>Evidence observation<textarea name="evidenceObservation" placeholder="Observe the order payment status" /></label><button type="submit">Create draft</button></form></article><article className="list"><h2>Scenarios <span>{scenarios.length}</span></h2>{scenarios.length ? scenarios.map((scenario) => <button className={`scenario ${selected?.id === scenario.id ? "selected" : ""}`} key={scenario.id} onClick={() => void openScenario(scenario.id)}><span className={`badge ${scenario.status}`}>{scenario.status.replace("_", " ")}</span><b>{scenario.title}</b><p>{scenario.businessGoal}</p><small>v{scenario.version} · {scenario.riskLevel} risk · {scenario.owner}</small></button>) : <p>No scenarios match the current filters.</p>}</article></div><div className="detail">{selected ? <><article><div className="detail-head"><div><span className={`badge ${selected.status}`}>{selected.status.replace("_", " ")}</span><h2>{selected.title}</h2><p>Version {selected.version} · revision {selected.revision}</p></div><div className="actions">{selected.status === "draft" && <button onClick={() => void transition("submit")}>Submit review</button>}{selected.status === "in_review" && <><button className="secondary" onClick={() => void comment()}>Add comment</button><button className="danger" onClick={() => void transition("reject")}>Reject</button><button onClick={() => void transition("approve")}>Approve</button></>}</div></div>{selected.status !== "in_review" && <form onSubmit={save}><label>Title<input name="title" defaultValue={selected.title} /></label><label>Business goal<textarea name="businessGoal" defaultValue={selected.businessGoal} /></label><label>Preconditions <small>one per line</small><textarea name="preconditions" defaultValue={join(selected.preconditions)} /></label><label>Business invariants <small>one per line</small><textarea name="invariants" defaultValue={join(selected.invariants)} /></label><label>Tags<input name="tags" defaultValue={selected.tags.join(", ")} /></label><button type="submit">Save as new version</button></form>}{selected.status === "in_review" && <label>Review comment or rejection reason<textarea value={review} onChange={(event) => setReview(event.target.value)} placeholder="Describe the requested change" /></label>}</article><article><h2>Evidence plan</h2>{selected.evidencePoints.length ? selected.evidencePoints.map((evidence, index) => <div className="evidence" key={`${evidence.target}-${index}`}><b>{evidence.channel.toUpperCase()} · {evidence.target}</b><p>{evidence.observation}</p><small>{evidence.assertions.length} assertion(s)</small></div>) : <p>No evidence points yet.</p>}<h2>Review</h2>{selected.reviews.length ? selected.reviews.map((item) => <div className="review" key={item.id}><b>{item.author}</b><span>{item.status}</span><p>{item.content}</p></div>) : <p>No review comments.</p>}<h2>Version history</h2>{versions.map((version) => <div className="version" key={version.version}><b>v{version.version}</b><span>{version.status}</span><small>{version.updatedAt}</small></div>)}{versions.length > 1 && <button className="secondary" onClick={() => void compare()}>Compare latest versions</button>}{diff && <div className="diff">{diff.changes.length ? diff.changes.map((change) => <p key={change.field}><b>{change.field}</b>: {JSON.stringify(change.from)} → {JSON.stringify(change.to)}</p>) : <p>No content changes.</p>}</div>}</article>{selected.status === "approved" && <ExecutionPanel scenario={selected} onError={setError} />}</> : <article className="empty"><h2>Select a scenario</h2><p>Open a scenario to edit its draft, review evidence, or compare versions.</p>{health && <small>{health.services.filter((item) => item.status !== "ready").length ? "Some services are degraded." : "All connected services are ready."}</small>}</article>}</div></div></section></main>;
}

createRoot(document.getElementById("root")!).render(window.location.pathname === "/foundation" || window.location.pathname === "/signin-callback" ? <FoundationApp /> : <App />);
