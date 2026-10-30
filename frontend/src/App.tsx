import { type FormEvent, useState } from "react";

import { WorkflowList } from "./WorkflowList";

export function App() {
  const [token, setToken] = useState("");
  const [draft, setDraft] = useState("");

  function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = draft.trim();
    if (next) setToken(next);
  }

  if (token) {
    return <WorkflowList token={token} onSignOut={() => {
      setToken("");
      setDraft("");
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
