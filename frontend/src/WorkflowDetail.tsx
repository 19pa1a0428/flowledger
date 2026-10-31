import { useEffect, useState } from "react";

import { ApiError, fetchWorkflow, type WorkflowDetail } from "./api";

interface WorkflowDetailProps {
  token: string;
  workflowId: string;
  onBack: () => void;
  onSignOut: () => void;
}

function displayDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function WorkflowDetailView({
  token,
  workflowId,
  onBack,
  onSignOut,
}: WorkflowDetailProps) {
  const [workflow, setWorkflow] = useState<WorkflowDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [request, setRequest] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchWorkflow(token, workflowId, controller.signal)
      .then(setWorkflow)
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof ApiError && reason.status === 404
          ? "Workflow not found or unavailable."
          : reason instanceof Error ? reason.message : "Unable to load workflow");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [request, token, workflowId]);

  return (
    <main className="shell" aria-busy={loading}>
      <header className="masthead">
        <div>
          <button className="text-button" type="button" onClick={onBack}>← Back to workflows</button>
          <p className="eyebrow">Workflow detail</p>
          <h1>{workflow?.name ?? "FlowLedger"}</h1>
        </div>
        <button className="secondary" type="button" onClick={onSignOut}>Sign out</button>
      </header>

      <div aria-live="polite">
        {loading && <p className="state" role="status">Loading workflow history…</p>}
        {!loading && error && (
          <div className="state error" role="alert">
            <p>{error}</p>
            <button type="button" onClick={() => setRequest((value) => value + 1)}>Try again</button>
          </div>
        )}
      </div>

      {!loading && !error && workflow && (
        <>
          <section className="panel summary" aria-labelledby="summary-heading">
            <div>
              <h2 id="summary-heading">Current state</h2>
              <span className={`badge ${workflow.status}`}>{workflow.status}</span>
            </div>
            <dl>
              <div><dt>Owner</dt><dd>{workflow.owner_id}</dd></div>
              <div><dt>Attempts</dt><dd>{workflow.attempts} / {workflow.max_attempts}</dd></div>
              <div><dt>Version</dt><dd>{workflow.version}</dd></div>
              <div><dt>Workflow ID</dt><dd className="identifier">{workflow.id}</dd></div>
            </dl>
          </section>

          <section className="panel chronology" aria-labelledby="history-heading">
            <h2 id="history-heading">Event history</h2>
            <p>Oldest to newest. Every state change records its actor and attempt.</p>
            <ol className="timeline">
              {workflow.events.map((event, index) => (
                <li key={`${event.occurred_at}-${index}`}>
                  <div className="timeline-marker" aria-hidden="true" />
                  <div>
                    <h3>{event.status}</h3>
                    <p><time dateTime={event.occurred_at}>{displayDate(event.occurred_at)}</time></p>
                    <dl>
                      <div><dt>Actor</dt><dd>{event.actor_id}</dd></div>
                      <div><dt>Attempt</dt><dd>{event.attempt}</dd></div>
                    </dl>
                  </div>
                </li>
              ))}
            </ol>
          </section>
        </>
      )}
    </main>
  );
}
