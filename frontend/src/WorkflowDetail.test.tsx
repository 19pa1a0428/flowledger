import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkflowDetailView } from "./WorkflowDetail";

afterEach(() => cleanup());

const id = "123e4567-e89b-42d3-a456-426614174000";
const detail = {
  id,
  name: "Synthetic report",
  owner_id: "synthetic-viewer",
  status: "running",
  attempts: 1,
  max_attempts: 3,
  version: 1,
  events: [
    { status: "pending", occurred_at: "2026-10-30T20:00:00Z", attempt: 0,
      actor_id: "synthetic-viewer" },
    { status: "running", occurred_at: "2026-10-30T20:01:00Z", attempt: 1,
      actor_id: "synthetic-operator" },
  ],
};

describe("WorkflowDetailView", () => {
  it("renders current state and an ordered accessible chronology", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(
      JSON.stringify(detail), { status: 200 },
    ))));
    render(<WorkflowDetailView token="synthetic-token" workflowId={id}
      onBack={() => undefined} onSignOut={() => undefined} />);
    expect(screen.getByRole("status").textContent).toContain("Loading");
    expect(await screen.findByRole("heading", { name: "Event history" })).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("synthetic-operator")).toBeTruthy();
    expect(screen.getByText(id)).toBeTruthy();
  });

  it("uses one unavailable message for a missing or unauthorized workflow", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(
      JSON.stringify({ detail: "Workflow not found" }), { status: 404 },
    ))));
    render(<WorkflowDetailView token="synthetic-token" workflowId={id}
      onBack={() => undefined} onSignOut={() => undefined} />);
    expect((await screen.findByRole("alert")).textContent)
      .toContain("Workflow not found or unavailable.");
  });
});
