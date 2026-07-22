import { FormEvent, useEffect, useState } from "react";
import { api, Diff, EvidencePoint, RiskLevel, Scenario, ScenarioStatus, ScenarioVersion } from "./api";
import { ExecutionPanel } from "./ExecutionPanel";

const levels: RiskLevel[] = ["low", "medium", "high", "critical"];
const statuses: ScenarioStatus[] = ["draft", "in_review", "approved", "rejected", "archived", "deprecated"];
const split = (value: string) => value.split("\n").map((item) => item.trim()).filter(Boolean);
const csv = (value: FormDataEntryValue | null) => String(value || "").split(",").map((item) => item.trim()).filter(Boolean);
const input = (name: string, value?: string | number) => <input name={name} defaultValue={value} />;

function scenarioPayload(form: HTMLFormElement) {
  const data = new FormData(form);
  const value = (name: string) => String(data.get(name) || "").trim();
  const target = value("evidenceTarget");
  const observation = value("evidenceObservation");
  const title = value("riskTitle");
  const evidencePoints: EvidencePoint[] = target && observation ? [{
    channel: value("evidenceChannel") as EvidencePoint["channel"], target, observation,
    assertions: [{ path: value("assertionPath") || "status", operator: value("assertionOperator") || "equals", expected: value("assertionExpected") }], required: true,
  }] : [];
  return {
    title: data.get("title"), businessGoal: data.get("businessGoal"), actors: csv(data.get("actors")), owner: value("owner") || undefined,
    riskLevel: data.get("riskLevel"), tags: csv(data.get("tags")), preconditions: split(value("preconditions")), invariants: split(value("invariants")),
    risks: title ? [{ title, description: value("riskDescription"), level: data.get("riskItemLevel") }] : [], evidencePoints,
  };
}

export function ValidationApp() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selected, setSelected] = useState<Scenario | null>(null);
  const [etag, setEtag] = useState("");
  const [versions, setVersions] = useState<ScenarioVersion[]>([]);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [filters, setFilters] = useState({ q: "", status: "", risk: "", tag: "", owner: "" });
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [degraded, setDegraded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [review, setReview] = useState("");
  const [fromVersion, setFromVersion] = useState("");
  const [toVersion, setToVersion] = useState("");

  const refresh = async (nextPage = page) => {
    try {
      setError("");
      const result = await api.scenarios({ ...filters, page: String(nextPage), pageSize: "20" });
      setScenarios(result.data.items); setTotal(result.data.total); setPage(result.data.page); setDegraded(result.degraded);
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to load validation scenarios"); }
  };
  const open = async (id: string) => {
    try {
      setError("");
      const [detail, history] = await Promise.all([api.scenario(id), api.versions(id)]);
      setSelected(detail.data); setEtag(detail.etag || ""); setVersions(history.data); setDiff(null); setReview("");
      setFromVersion(String(history.data[0]?.version || "")); setToVersion(String(history.data.at(-1)?.version || ""));
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to open scenario"); }
  };
  useEffect(() => { void refresh(1); }, []);

  const written = async (scenario: Scenario, nextEtag: string | undefined, message: string) => {
    setNotice(message); setSelected(scenario); setEtag(nextEtag || ""); await refresh(); await open(scenario.id);
  };
  const failure = (err: unknown) => {
    const message = err instanceof Error ? err.message : "Request failed";
    setError(message.includes("updated") ? `${message} Reload the scenario before trying again.` : message);
  };
  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    try { const result = await api.createScenario(scenarioPayload(event.currentTarget)); event.currentTarget.reset(); await written(result.data, result.etag, "Scenario created as draft"); } catch (err) { failure(err); }
  };
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); if (!selected) return;
    try { const result = await api.updateScenario(selected.id, scenarioPayload(event.currentTarget), etag); await written(result.data, result.etag, "Saved as a new version"); } catch (err) { failure(err); }
  };
  const transition = async (action: "submit" | "approve" | "reject" | "archive" | "deprecate") => {
    if (!selected) return;
    try {
      const result = action === "submit" ? await api.submitReview(selected.id, etag) : action === "approve" ? await api.approve(selected.id, etag) : action === "reject" ? await api.reject(selected.id, review || "Changes requested", etag) : action === "archive" ? await api.archive(selected.id, etag) : await api.deprecate(selected.id, etag);
      setReview(""); await written(result.data, result.etag, `Scenario ${action}d`);
    } catch (err) { failure(err); }
  };
  const addReview = async () => {
    if (!selected || !review.trim()) return;
    try { const result = await api.addReview(selected.id, review.trim(), etag); setReview(""); await written(result.data, result.etag, "Review comment added"); } catch (err) { failure(err); }
  };
  const resolveReview = async (reviewId: string) => {
    if (!selected) return;
    try { const result = await api.resolveReview(selected.id, reviewId, etag); await written(result.data, result.etag, "Review comment resolved"); } catch (err) { failure(err); }
  };
  const compare = async () => {
    if (!selected || !fromVersion || !toVersion || fromVersion === toVersion) return;
    try { setDiff((await api.diff(selected.id, Number(fromVersion), Number(toVersion))).data); } catch (err) { failure(err); }
  };
  const editor = (scenario?: Scenario) => <form key={scenario?.revision || "new"} onSubmit={scenario ? save : create}>
    <label>Title{input("title", scenario?.title)}</label>
    <label>Business goal<textarea name="businessGoal" defaultValue={scenario?.businessGoal} required /></label>
    <div className="pair"><label>Actors{input("actors", scenario?.actors.join(", "))}</label><label>Owner{input("owner", scenario?.owner)}</label></div>
    <div className="pair"><label>Risk level<select name="riskLevel" defaultValue={scenario?.riskLevel || "medium"}>{levels.map((item) => <option key={item}>{item}</option>)}</select></label><label>Tags{input("tags", scenario?.tags.join(", "))}</label></div>
    <label>Preconditions <small>one per line</small><textarea name="preconditions" defaultValue={scenario?.preconditions.join("\n")} /></label>
    <label>Business invariants <small>one per line</small><textarea name="invariants" defaultValue={scenario?.invariants.join("\n")} /></label>
    <h3>Risk</h3><label>Title{input("riskTitle", scenario?.risks[0]?.title)}</label><label>Description<textarea name="riskDescription" defaultValue={scenario?.risks[0]?.description} /></label><label>Level<select name="riskItemLevel" defaultValue={scenario?.risks[0]?.level || "medium"}>{levels.map((item) => <option key={item}>{item}</option>)}</select></label>
    <h3>Evidence point</h3><div className="pair"><label>Channel<select name="evidenceChannel" defaultValue={scenario?.evidencePoints[0]?.channel || "state"}>{(["ui", "api", "state"] as const).map((item) => <option key={item}>{item}</option>)}</select></label><label>Target{input("evidenceTarget", scenario?.evidencePoints[0]?.target)}</label></div>
    <label>Observation<textarea name="evidenceObservation" defaultValue={scenario?.evidencePoints[0]?.observation} /></label><div className="pair"><label>Assertion path{input("assertionPath", scenario?.evidencePoints[0]?.assertions[0]?.path)}</label><label>Operator{input("assertionOperator", scenario?.evidencePoints[0]?.assertions[0]?.operator)}</label></div><label>Expected value{input("assertionExpected", String(scenario?.evidencePoints[0]?.assertions[0]?.expected || ""))}</label>
    <button type="submit">{scenario ? "Save as new version" : "Create draft"}</button>
  </form>;

  return <main><aside><div className="brand">OpenKATE</div><span>v0.2.0 · Validation Center</span><nav><b>Validation</b><a className="active">Scenarios</a><a>Evidence plan</a><a>Reviews</a></nav></aside><section><header><div><p>PROJECT_DEMO / VALIDATION</p><h1>Business validation</h1><span>Version scenarios, review evidence and approve the validation asset.</span></div><button onClick={() => void refresh()}>Refresh</button></header>{error && <div className="error">{error}{selected && <button className="secondary" onClick={() => void open(selected.id)}>Reload scenario</button>}</div>}{notice && <div className="notice">{notice}</div>}{degraded && <div className="warning">Scenario list is temporarily served from the validation service while the reporting read model catches up.</div>}<div className="toolbar"><input aria-label="Search scenarios" value={filters.q} onChange={(event) => setFilters({ ...filters, q: event.target.value })} placeholder="Search title or business goal" /><select aria-label="Filter status" value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value })}><option value="">All statuses</option>{statuses.map((item) => <option key={item}>{item}</option>)}</select><select aria-label="Filter risk" value={filters.risk} onChange={(event) => setFilters({ ...filters, risk: event.target.value })}><option value="">All risks</option>{levels.map((item) => <option key={item}>{item}</option>)}</select><input aria-label="Filter tag" value={filters.tag} onChange={(event) => setFilters({ ...filters, tag: event.target.value })} placeholder="Tag" /><input aria-label="Filter owner" value={filters.owner} onChange={(event) => setFilters({ ...filters, owner: event.target.value })} placeholder="Owner" /><button onClick={() => void refresh(1)}>Apply</button></div><div className="workspace"><div className="column"><article><h2>Create scenario</h2>{editor()}</article><article className="list"><h2>Scenarios <span>{total}</span></h2>{scenarios.map((scenario) => <button className={`scenario ${selected?.id === scenario.id ? "selected" : ""}`} key={scenario.id} onClick={() => void open(scenario.id)}><span className={`badge ${scenario.status}`}>{scenario.status.replace("_", " ")}</span><b>{scenario.title}</b><p>{scenario.businessGoal}</p><small>v{scenario.version} · {scenario.riskLevel} risk · {scenario.owner}</small></button>)}{!scenarios.length && <p>No scenarios match the current filters.</p>}{total > 20 && <div className="actions"><button disabled={page === 1} onClick={() => void refresh(page - 1)}>Previous</button><span>Page {page}</span><button disabled={page * 20 >= total} onClick={() => void refresh(page + 1)}>Next</button></div>}</article></div><div className="detail">{selected ? <><article><div className="detail-head"><div><span className={`badge ${selected.status}`}>{selected.status.replace("_", " ")}</span><h2>{selected.title}</h2><p>Version {selected.version} · revision {selected.revision}</p></div><div className="actions">{selected.status === "draft" && <button onClick={() => void transition("submit")}>Submit review</button>}{selected.status === "in_review" && <><button className="secondary" onClick={() => void addReview()}>Add comment</button><button className="danger" onClick={() => void transition("reject")}>Reject</button><button onClick={() => void transition("approve")}>Approve</button></>}{selected.status === "approved" && <><button className="secondary" onClick={() => void transition("deprecate")}>Deprecate</button><button className="danger" onClick={() => void transition("archive")}>Archive</button></>}</div></div>{selected.status !== "in_review" && selected.status !== "archived" && selected.status !== "deprecated" ? editor(selected) : selected.status === "in_review" && <label>Review comment or rejection reason<textarea value={review} onChange={(event) => setReview(event.target.value)} /></label>}</article><article><h2>Evidence plan</h2>{selected.evidencePoints.map((item, index) => <div className="evidence" key={`${item.target}-${index}`}><b>{item.channel.toUpperCase()} · {item.target}</b><p>{item.observation}</p><small>{item.assertions.length} assertion(s)</small></div>)}{!selected.evidencePoints.length && <p>No evidence points yet.</p>}<h2>Review</h2>{selected.reviews.map((item) => <div className="review" key={item.id}><b>{item.author}</b><span>{item.status}</span><p>{item.content}</p>{item.status === "open" && selected.status === "in_review" && <button className="secondary" onClick={() => void resolveReview(item.id)}>Resolve</button>}</div>)}{!selected.reviews.length && <p>No review comments.</p>}<h2>Version history</h2><div className="pair"><label>From<select value={fromVersion} onChange={(event) => setFromVersion(event.target.value)}>{versions.map((item) => <option key={item.version} value={item.version}>v{item.version}</option>)}</select></label><label>To<select value={toVersion} onChange={(event) => setToVersion(event.target.value)}>{versions.map((item) => <option key={item.version} value={item.version}>v{item.version}</option>)}</select></label></div><button className="secondary" disabled={fromVersion === toVersion} onClick={() => void compare()}>Compare versions</button>{diff && <div className="diff">{diff.changes.length ? diff.changes.map((change) => <p key={change.field}><b>{change.field}</b>: {JSON.stringify(change.from)} → {JSON.stringify(change.to)}</p>) : <p>No content changes.</p>}</div>}</article>{selected.status === "approved" && <ExecutionPanel scenario={selected} onError={setError} />}</> : <article className="empty"><h2>Select a scenario</h2><p>Open a scenario to edit its draft, review evidence, or compare versions.</p></article>}</div></div></section></main>;
}
