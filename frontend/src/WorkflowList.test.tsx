import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkflowList } from "./WorkflowList";

afterEach(() => cleanup());

function response(items: unknown[], next_cursor: string | null = null) {
  return Promise.resolve(new Response(JSON.stringify({ items, next_cursor }), {
    status: 200, headers: { "Content-Type": "application/json" },
  }));
}

const item = {
  id: "123e4567-e89b-42d3-a456-426614174000",
  name: "Synthetic report",
  owner_id: "synthetic-viewer",
  status: "pending",
  attempts: 0,
  max_attempts: 3,
  version: 0,
  created_at: "2026-10-29T20:00:00Z",
  updated_at: "2026-10-29T20:00:00Z",
};

describe("WorkflowList", () => {
  it("announces loading and renders an accessible workflow table", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response([item])));
    render(<WorkflowList token="synthetic-token" onSignOut={() => undefined} />);
    expect(screen.getByRole("status").textContent).toContain("Loading");
    expect(await screen.findByRole("table", { name: "Authorized workflows" })).toBeTruthy();
    expect(screen.getByRole("rowheader", { name: /Synthetic report/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Previous" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Next" }).hasAttribute("disabled")).toBe(true);
  });

  it("renders empty and retryable error states", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockRejectedValueOnce(new Error("Network unavailable"))
      .mockImplementationOnce(() => response([]));
    vi.stubGlobal("fetch", fetchMock);
    render(<WorkflowList token="synthetic-token" onSignOut={() => undefined} />);
    expect((await screen.findByRole("alert")).textContent).toContain("Network unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByText("No workflows match this view.")).toBeTruthy());
  });
});
