import { useEffect, useState } from "react";

import {
  fetchWorkflows,
  WORKFLOW_STATUSES,
  type WorkflowPage,
  type WorkflowStatus,
} from "./api";

interface WorkflowListProps {
  token: string;
  onSignOut: () => void;
  onOpenWorkflow: (workflowId: string) => void;
}

function displayDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function WorkflowList({ token, onSignOut, onOpenWorkflow }: WorkflowListProps) {
  const [page, setPage] = useState<WorkflowPage | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [history, setHistory] = useState<(string | null)[]>([]);
  const [status, setStatus] = useState<WorkflowStatus | "">("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [request, setRequest] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchWorkflows({ token, cursor, status, signal: controller.signal })
      .then(setPage)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Unable to load workflows");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [cursor, request, status, token]);

  function chooseStatus(next: WorkflowStatus | "") {
    setStatus(next);
    setCursor(null);
    setHistory([]);
  }

  function nextPage() {
    if (!page?.next_cursor) return;
    setHistory((current) => [...current, cursor]);
    setCursor(page.next_cursor);
  }

  function previousPage() {
    const previous = history.at(-1);
    if (previous === undefined) return;
    setHistory((current) => current.slice(0, -1));
    setCursor(previous);
  }

  return (
    <main className="shell" aria-busy={loading}>
      <header className="masthead">
        <div>
          <p className="eyebrow">Workflow operations</p>
          <h1>FlowLedger</h1>
        </div>
        <button className="secondary" type="button" onClick={onSignOut}>Sign out</button>
      </header>

      <section className="panel" aria-labelledby="workflow-heading">
        <div className="toolbar">
          <div>
            <h2 id="workflow-heading">Workflows</h2>
            <p>Newest activity-ready records in your authorized scope.</p>
          </div>
          <label>
            Status
            <select value={status} onChange={(event) => chooseStatus(
              event.target.value as WorkflowStatus | ""
            )}>
              <option value="">All statuses</option>
              {WORKFLOW_STATUSES.map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </label>
        </div>

        <div aria-live="polite">
          {loading && <p className="state" role="status">Loading workflows…</p>}
          {!loading && error && (
            <div className="state error" role="alert">
              <p>{error}</p>
              <button type="button" onClick={() => setRequest((value) => value + 1)}>
                Try again
              </button>
            </div>
          )}
          {!loading && !error && page?.items.length === 0 && (
            <p className="state">No workflows match this view.</p>
          )}
        </div>

        {!loading && !error && page && page.items.length > 0 && (
          <div className="table-wrap">
            <table>
              <caption className="sr-only">Authorized workflows</caption>
              <thead>
                <tr><th scope="col">Name</th><th scope="col">Status</th>
                  <th scope="col">Attempts</th><th scope="col">Updated</th></tr>
              </thead>
              <tbody>
                {page.items.map((workflow) => (
                  <tr key={workflow.id}>
                    <th scope="row">
                      <a href={`/workflows/${workflow.id}`} onClick={(event) => {
                        event.preventDefault();
                        onOpenWorkflow(workflow.id);
                      }}>{workflow.name}</a>
                      <small>{workflow.id}</small>
                    </th>
                    <td><span className={`badge ${workflow.status}`}>{workflow.status}</span></td>
                    <td>{workflow.attempts} / {workflow.max_attempts}</td>
                    <td><time dateTime={workflow.updated_at}>{displayDate(workflow.updated_at)}</time></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {!loading && !error && page && (
          <nav className="pagination" aria-label="Workflow pages">
            <button type="button" className="secondary" disabled={history.length === 0}
              onClick={previousPage}>Previous</button>
            <span aria-live="polite">Page {history.length + 1}</span>
            <button type="button" disabled={!page.next_cursor} onClick={nextPage}>Next</button>
          </nav>
        )}
      </section>
    </main>
  );
}
