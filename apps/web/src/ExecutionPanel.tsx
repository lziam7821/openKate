import { FormEvent, useEffect, useState } from "react";
import { api, Environment, ExecutionPlan, ExecutionRun, RunEvents, Scenario } from "./api";

type Props = { scenario: Scenario; onError: (message: string) => void };

export function ExecutionPanel({ scenario, onError }: Props) {
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [environmentId, setEnvironmentId] = useState("");
  const [plan, setPlan] = useState<ExecutionPlan | null>(null);
  const [run, setRun] = useState<ExecutionRun | null>(null);
  const [events, setEvents] = useState<RunEvents["events"]>([]);

  const loadEnvironments = async () => {
    try {
      const result = await api.environments(scenario.projectId);
      setEnvironments(result.data);
      if (!environmentId && result.data.length) setEnvironmentId(result.data[0].id);
    } catch (error) { onError(error instanceof Error ? error.message : "Unable to load environments"); }
  };

  useEffect(() => { void loadEnvironments(); }, [scenario.id]);
  useEffect(() => {
    if (!run || run.status !== "running") return;
    const timer = window.setInterval(async () => {
      try {
        const [nextRun, nextEvents] = await Promise.all([api.run(run.id), api.runEvents(run.id)]);
        setRun(nextRun.data); setEvents(nextEvents.data.events);
      } catch (error) { onError(error instanceof Error ? error.message : "Unable to refresh run"); }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [run?.id, run?.status]);

  const createEnvironment = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const result = await api.createEnvironment(scenario.projectId, {
        name: form.get("name"), base_url: form.get("baseUrl"), write_policy: "read_only",
        allowed_hosts: String(form.get("allowedHosts")).split(",").map((item) => item.trim()).filter(Boolean),
        account_refs: String(form.get("accountRefs") || "").split(",").map((item) => item.trim()).filter(Boolean),
        data_set_refs: String(form.get("dataSetRefs") || "").split(",").map((item) => item.trim()).filter(Boolean),
        secret_refs: { database: String(form.get("databaseSecretRef") || "") },
      });
      setEnvironmentId(result.data.id); await loadEnvironments(); event.currentTarget.reset();
    } catch (error) { onError(error instanceof Error ? error.message : "Unable to create environment"); }
  };

  const createPlan = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const result = await api.createExecutionPlan(scenario.id, {
        variables: { sku: String(form.get("sku")) }, timeoutMs: 120000,
        steps: [
          { id: "place_order", channel: "ui", action: "sequence", input: { url: form.get("shopUrl"), actions: [{ type: "fill", selector: form.get("skuSelector"), value: "{{ sku }}" }, { type: "click", selector: form.get("submitSelector") }, { type: "waitFor", selector: form.get("resultSelector") }, { type: "extractAttribute", selector: form.get("resultSelector"), attribute: "data-order-id", saveAs: "orderId" }] }, save: { orderId: "orderId" }, timeoutMs: 30000, idempotent: true },
          { id: "pay_order", channel: "api", action: "request", dependsOn: ["place_order"], input: { url: `${String(form.get("paymentUrl")).replace(/\/$/, "")}/orders/{{ orderId }}/pay`, method: "POST", json: { orderId: "{{ orderId }}" }, assertions: [{ path: "body.status", operator: "equals", expected: "PAID" }] }, save: {}, timeoutMs: 10000, idempotent: true },
          { id: "verify_order", channel: "state", action: "query", dependsOn: ["pay_order"], input: { connectionSecretRef: form.get("databaseSecretRef"), query: "SELECT status FROM orders WHERE id = %(order_id)s", params: { order_id: "{{ orderId }}" }, assertions: [{ path: "rows.0.status", operator: "equals", expected: "PAID" }] }, save: {}, timeoutMs: 10000, idempotent: true },
        ],
      });
      setPlan(result.data);
    } catch (error) { onError(error instanceof Error ? error.message : "Unable to create execution plan"); }
  };

  const startRun = async () => {
    if (!plan || !environmentId) return;
    try { const result = await api.createRun(scenario.id, { planId: plan.id, environmentId }); setRun(result.data); setEvents([]); }
    catch (error) { onError(error instanceof Error ? error.message : "Unable to start run"); }
  };

  const cancel = async () => {
    if (!run) return;
    try { await api.cancelRun(run.id); setRun((await api.run(run.id)).data); }
    catch (error) { onError(error instanceof Error ? error.message : "Unable to cancel run"); }
  };

  const retry = async () => {
    if (!run) return;
    try { const result = await api.retryRun(run.id); setRun(result.data); setEvents([]); }
    catch (error) { onError(error instanceof Error ? error.message : "Unable to retry run"); }
  };

  return <article className="execution"><div className="detail-head"><div><span className="eyebrow">EXECUTION FABRIC</span><h2>UI → API → State</h2><p>Build a plan from approved scenario v{scenario.version}, then run it in an isolated environment.</p></div>{run && <span className={`run-status ${run.status}`}>{run.status}</span>}</div><details><summary>Environment and resource pool</summary><form onSubmit={createEnvironment}><div className="pair"><label>Name<input name="name" required placeholder="Staging" /></label><label>Base URL<input name="baseUrl" required placeholder="https://shop.test" /></label></div><label>Allowed hosts<input name="allowedHosts" required placeholder="shop.test, payments.test" /></label><div className="pair"><label>Account references<input name="accountRefs" placeholder="vault://accounts/qa-1" /></label><label>Dataset references<input name="dataSetRefs" placeholder="dataset://checkout-1" /></label></div><label>Database secret reference<input name="databaseSecretRef" required placeholder="staging-db" /></label><button type="submit" className="secondary">Save environment</button></form></details><form onSubmit={createPlan}><div className="pair"><label>Environment<select value={environmentId} onChange={(event) => setEnvironmentId(event.target.value)} required><option value="">Select environment</option>{environments.map((environment) => <option value={environment.id} key={environment.id}>{environment.name}</option>)}</select></label><label>Test SKU<input name="sku" defaultValue="SKU-1" required /></label></div><div className="pair"><label>Shop URL<input name="shopUrl" required placeholder="https://shop.test/checkout" /></label><label>Payment API URL<input name="paymentUrl" required placeholder="https://payments.test" /></label></div><div className="pair"><label>SKU selector<input name="skuSelector" defaultValue="#sku" required /></label><label>Submit selector<input name="submitSelector" defaultValue="#submit" required /></label></div><div className="pair"><label>Result selector<input name="resultSelector" defaultValue="#result" required /></label><label>Database secret reference<input name="databaseSecretRef" defaultValue="staging-db" required /></label></div><button type="submit">Create execution plan</button></form>{plan && <div className="plan"><h2>Execution plan <span>v{plan.version}</span></h2>{plan.steps.map((step) => <div className="plan-step" key={step.id}><span>{step.channel.toUpperCase()}</span><b>{step.id}</b><small>{step.dependsOn.length ? `after ${step.dependsOn.join(", ")}` : "entry step"}</small></div>)}<button disabled={!environmentId} onClick={() => void startRun()}>Run plan</button></div>}{run && <div className="timeline"><div className="detail-head"><h2>Run timeline · attempt {run.attempt}</h2><div className="actions">{run.status === "running" && <button className="danger" onClick={() => void cancel()}>Cancel</button>}{(["failed", "canceled"] as string[]).includes(run.status) && <button onClick={() => void retry()}>Retry</button>}</div></div>{run.stepResults.map((step) => <div className={`timeline-step ${step.status}`} key={step.stepId}><span></span><div><b>{step.stepId}</b><p>{step.status}{step.error ? ` · ${step.error.category}: ${step.error.message}` : ""}</p><small>{step.evidenceRefs.length} evidence artifact(s)</small></div></div>)}<div className="events"><b>Live events</b>{events.slice(-6).map((event) => <p key={event.eventId}>{event.eventType}<small>{new Date(event.occurredAt).toLocaleTimeString()}</small></p>)}</div></div>}</article>;
}
