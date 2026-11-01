import { useEffect, useState } from "react";

import {
  ApiError,
  fetchWorkflow,
  transitionWorkflow,
  type WorkflowDetail,
  type WorkflowStatus,
} from "./api";

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

const ACTIONS: Partial<Record<WorkflowStatus, { status: WorkflowStatus; label: string }[]>> = {
  pending: [
    { status: "running", label: "Start workflow" },
    { status: "cancelled", label: "Cancel workflow" },
  ],
  running: [
    { status: "succeeded", label: "Mark succeeded" },
    { status: "failed", label: "Mark failed" },
  ],
  failed: [
    { status: "pending", label: "Retry workflow" },
    { status: "cancelled", label: "Cancel workflow" },
  ],
};

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
  const [submitting, setSubmitting] = useState<WorkflowStatus | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

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

  async function applyTransition(status: WorkflowStatus) {
    if (!workflow || submitting) return;
    const controller = new AbortController();
    setSubmitting(status);
    setActionMessage(null);
    setActionError(null);
    try {
      const updated = await transitionWorkflow(
        token, workflow.id, status, workflow.version, controller.signal,
      );
      setWorkflow(updated);
      setActionMessage(`Workflow moved to ${updated.status}.`);
    } catch (reason: unknown) {
      if (reason instanceof ApiError && reason.status === 409) {
        try {
          setWorkflow(await fetchWorkflow(token, workflow.id, controller.signal));
          setActionError("Workflow changed before this action completed. Latest state loaded; review and try again.");
        } catch {
          setActionError("Workflow changed, but the latest state could not be loaded. Try again.");
        }
      } else if (reason instanceof ApiError && reason.status === 403) {
        setActionError("Operator role required for workflow actions.");
      } else {
        setActionError(reason instanceof Error ? reason.message : "Unable to update workflow");
      }
    } finally {
      setSubmitting(null);
    }
  }

  const actions = workflow ? (ACTIONS[workflow.status] ?? []).filter((action) =>
    action.status !== "pending" || workflow.attempts < workflow.max_attempts
  ) : [];

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

          <section className="panel actions" aria-labelledby="actions-heading">
            <div>
              <h2 id="actions-heading">Operator actions</h2>
              <p>Actions use version {workflow.version} to prevent overwriting a newer update.</p>
            </div>
            {actions.length > 0 ? (
              <div className="action-buttons">
                {actions.map((action) => (
                  <button type="button" key={action.status}
                    className={action.status === "cancelled" || action.status === "failed"
                      ? "danger" : undefined}
                    disabled={submitting !== null}
                    onClick={() => void applyTransition(action.status)}>
                    {submitting === action.status ? "Updating…" : action.label}
                  </button>
                ))}
              </div>
            ) : <p className="terminal-state">No actions are available for this state.</p>}
            <div aria-live="polite">
              {actionMessage && <p className="action-feedback success" role="status">{actionMessage}</p>}
              {actionError && <p className="action-feedback error" role="alert">{actionError}</p>}
            </div>
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
