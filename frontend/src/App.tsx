import { type FormEvent, useEffect, useState } from "react";

import { WorkflowDetailView } from "./WorkflowDetail";
import { WorkflowList } from "./WorkflowList";

const DETAIL_PATH = /^\/workflows\/([0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\/?$/i;

function detailIdFromLocation(): string | null {
  return window.location.pathname.match(DETAIL_PATH)?.[1] ?? null;
}

export function App() {
  const [token, setToken] = useState("");
  const [draft, setDraft] = useState("");
  const [workflowId, setWorkflowId] = useState(detailIdFromLocation);

  useEffect(() => {
    const onPopState = () => setWorkflowId(detailIdFromLocation());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = draft.trim();
    if (next) setToken(next);
  }

  if (token) {
    const signOut = () => {
      setToken("");
      setDraft("");
    };
    if (workflowId) {
      return <WorkflowDetailView token={token} workflowId={workflowId}
        onBack={() => {
          window.history.pushState(null, "", "/");
          setWorkflowId(null);
        }} onSignOut={signOut} />;
    }
    return <WorkflowList token={token} onSignOut={signOut}
      onOpenWorkflow={(id) => {
        window.history.pushState(null, "", `/workflows/${id}`);
        setWorkflowId(id);
      }} />;
  }

  return (
    <main className="sign-in-shell">
      <section className="sign-in" aria-labelledby="sign-in-title">
        <p className="eyebrow">Reliable workflow operations</p>
        <h1 id="sign-in-title">Open FlowLedger</h1>
        <p>Use a pre-provisioned bearer token. It stays only in this browser tab's memory.</p>
        <form onSubmit={signIn}>
          <label htmlFor="token">Bearer token</label>
          <input id="token" name="token" type="password" autoComplete="off"
            minLength={32} required value={draft}
            onChange={(event) => setDraft(event.target.value)} />
          <button type="submit">View workflows</button>
        </form>
      </section>
    </main>
  );
}
