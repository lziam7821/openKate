import { FormEvent, useEffect, useState } from "react";
import { api, BadCase, BusinessRule, KnowledgeItem, RuleMetrics } from "./api";

const ids = (value: FormDataEntryValue | null) => String(value || "").split(",").map((item) => item.trim()).filter(Boolean);

export function GovernanceApp() {
  const [projectId, setProjectId] = useState("project_demo");
  const [knowledge, setKnowledge] = useState<KnowledgeItem[]>([]);
  const [rules, setRules] = useState<BusinessRule[]>([]);
  const [badcase, setBadcase] = useState<BadCase | null>(null);
  const [selected, setSelected] = useState<BusinessRule | null>(null);
  const [metrics, setMetrics] = useState<RuleMetrics | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const fail = (value: unknown) => setError(value instanceof Error ? value.message : "Request failed");
  const refresh = async () => {
    try {
      const [items, ruleItems] = await Promise.all([api.knowledge(projectId, query), api.rules(projectId)]);
      setKnowledge(items.data.items); setRules(ruleItems.data.items);
    } catch (value) { fail(value); }
  };
  useEffect(() => { void refresh(); }, [projectId]);
  const openRule = async (id: string) => {
    try { const [rule, nextMetrics] = await Promise.all([api.rule(id), api.ruleMetrics(id)]); setSelected(rule.data); setMetrics(nextMetrics.data); } catch (value) { fail(value); }
  };
  const importKnowledge = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    try { await api.importKnowledge(projectId, { title: form.get("title"), content: form.get("content"), source: form.get("source"), category: form.get("category") }); event.currentTarget.reset(); setNotice("Knowledge imported with source and classification"); await refresh(); } catch (value) { fail(value); }
  };
  const createBadcase = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    try { const item = await api.createBadCase(String(form.get("runId")), { projectId, evidenceRefs: ids(form.get("evidenceRefs")), description: form.get("description") }); setBadcase(item.data); setNotice("BadCase recorded with evidence"); } catch (value) { fail(value); }
  };
  const createRule = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); if (!badcase) return; const form = new FormData(event.currentTarget);
    try { const item = await api.createRuleDraft(badcase.id, { projectId, scope: form.get("scope"), expectedEffect: form.get("expectedEffect"), riskLevel: form.get("riskLevel") }); setSelected(item.data); setNotice("Rule draft created from BadCase"); await refresh(); } catch (value) { fail(value); }
  };
  const action = async (name: "review" | "approve" | "replay" | "publish" | "rollback", payload?: object) => {
    if (!selected) return;
    try { await api.ruleAction(selected.id, name, payload); await openRule(selected.id); await refresh(); setNotice(`Rule ${name} completed`); } catch (value) { fail(value); }
  };
  const replay = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = new FormData(event.currentTarget); await action("replay", { runIds: ids(form.get("runIds")) }); };

  return <main><aside><div className="brand">OpenKATE</div><span>v0.6.0 · Knowledge & Governance</span><nav><b>Knowledge</b><a className="active">Knowledge import</a><a href="/">Validation</a><b>Governance</b><a>BadCases</a><a>Rules</a></nav></aside><section><header><div><p>PROJECT / GOVERNANCE</p><h1>Knowledge and rule governance</h1><span>Import evidence-backed knowledge, convert BadCases into reviewed rules, and replay before publication.</span></div><button onClick={() => void refresh()}>Refresh</button></header>{error && <div className="error">{error}</div>}{notice && <div className="notice">{notice}</div>}<div className="toolbar"><input aria-label="Project ID" value={projectId} onChange={(event) => setProjectId(event.target.value)} placeholder="Project ID" /><input aria-label="Search knowledge" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search knowledge" /><button onClick={() => void refresh()}>Search</button></div><div className="workspace"><div className="column"><article><h2>Import history</h2><form onSubmit={importKnowledge}><label>Title<input name="title" required /></label><label>Content<textarea name="content" required /></label><label>Source<input name="source" required placeholder="incident-123 or case-456" /></label><label>Category<select name="category"><option value="historical_defect">Historical defect</option><option value="historical_case">Historical case</option></select></label><button>Import knowledge</button></form></article><article><h2>Create BadCase</h2><form onSubmit={createBadcase}><label>Run ID<input name="runId" required /></label><label>Evidence references<input name="evidenceRefs" required placeholder="asset://trace-1, asset://screenshot-2" /></label><label>Manual correction<textarea name="description" required /></label><button>Create BadCase</button></form>{badcase && <small>Active BadCase: {badcase.id}</small>}</article><article className="list"><h2>Knowledge <span>{knowledge.length}</span></h2>{knowledge.map((item) => <div className="review" key={item.id}><b>{item.title}</b><span>{item.category}</span><p>{item.content}</p><small>{item.source}</small></div>)}{!knowledge.length && <p>No matching project knowledge.</p>}</article></div><div className="detail"><article><h2>Rule draft</h2>{badcase ? <form onSubmit={createRule}><label>Applicable scope<input name="scope" required placeholder="Payment refunds" /></label><label>Expected effect<textarea name="expectedEffect" required placeholder="Block invalid refund totals" /></label><label>Risk<select name="riskLevel"><option>low</option><option defaultValue="medium">medium</option><option>high</option><option>critical</option></select></label><button>Create from {badcase.id}</button></form> : <p>Create a BadCase first. Its evidence and correction become the rule source.</p>}</article><article><h2>Rules</h2>{rules.map((rule) => <button className={`scenario ${selected?.id === rule.id ? "selected" : ""}`} key={rule.id} onClick={() => void openRule(rule.id)}><span className={`badge ${rule.status}`}>{rule.status}</span><b>{rule.versions.at(-1)?.scope.description}</b><p>{rule.versions.at(-1)?.expectedEffect}</p><small>{rule.riskLevel} risk · v{rule.activeVersion || rule.versions.at(-1)?.version}</small></button>)}{!rules.length && <p>No rules for this project.</p>}</article>{selected && <><article><div className="detail-head"><div><span className={`badge ${selected.status}`}>{selected.status}</span><h2>Rule {selected.id}</h2><p>Source: {selected.badcaseId} · active version: {selected.activeVersion || "none"}</p></div><div className="actions">{selected.status === "draft" && <button onClick={() => void action("review", {})}>Submit review</button>}{selected.status === "in_review" && <button onClick={() => void action("approve")}>Approve</button>}{selected.status === "approved" && <button onClick={() => void action("publish")}>Publish</button>}{selected.status === "published" && <button className="danger" onClick={() => void action("rollback")}>Rollback</button>}</div></div><p>{selected.versions.at(-1)?.content}</p><form className="inline-form" onSubmit={replay}><input name="runIds" required placeholder="Historical run IDs, comma separated" /><button className="secondary">Replay history</button></form></article><article><h2>Effect metrics</h2>{metrics ? <div className="pair"><div><b>{(metrics.hitRate * 100).toFixed(0)}%</b><p>Hit rate</p></div><div><b>{(metrics.falsePositiveRate * 100).toFixed(0)}%</b><p>False-positive rate</p></div><div><b>{metrics.falseNegatives}</b><p>False negatives</p></div><div><b>{metrics.recentUsage.runs}</b><p>Historical runs</p></div></div> : <p>Open a rule to inspect its metrics.</p>}</article></>}</div></div></section></main>;
}
