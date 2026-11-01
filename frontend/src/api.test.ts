import { describe, expect, it, vi } from "vitest";

import {
  ApiError,
  fetchWorkflow,
  fetchWorkflows,
  parseWorkflowDetail,
  parseWorkflowPage,
  transitionWorkflow,
} from "./api";

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

describe("workflow API", () => {
  it("parses a valid page and rejects a malformed runtime payload", () => {
    expect(parseWorkflowPage({ items: [item], next_cursor: null }).items[0]).toEqual(item);
    expect(() => parseWorkflowPage({
      items: [{ ...item, attempts: "zero" }], next_cursor: null,
    })).toThrow("attempts");
  });

  it("sends authentication and pagination parameters", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({
      items: [item], next_cursor: item.id,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    await fetchWorkflows({
      token: "synthetic-token", cursor: item.id, status: "pending", fetcher,
    });
    expect(fetcher).toHaveBeenCalledWith(
      `/workflows?limit=10&cursor=${item.id}&status=pending`,
      expect.objectContaining({ headers: { Authorization: "Bearer synthetic-token" } }),
    );
  });

  it("preserves the API detail on an HTTP failure", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(
      JSON.stringify({ detail: "Not authenticated" }), { status: 401 },
    ));
    await expect(fetchWorkflows({ token: "bad-token", fetcher })).rejects.toEqual(
      new ApiError("Not authenticated", 401),
    );
  });

  it("parses detail history and rejects a snapshot/history mismatch", () => {
    const detail = { ...item, events: [{
      status: "pending", occurred_at: item.created_at, attempt: 0,
      actor_id: "synthetic-viewer",
    }] };
    expect(parseWorkflowDetail(detail).events).toHaveLength(1);
    expect(() => parseWorkflowDetail({ ...detail, status: "running" }))
      .toThrow("event history");
  });

  it("fetches one workflow with the bearer token", async () => {
    const detail = { ...item, events: [{
      status: "pending", occurred_at: item.created_at, attempt: 0,
      actor_id: "synthetic-viewer",
    }] };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(
      JSON.stringify(detail), { status: 200 },
    ));
    await fetchWorkflow("synthetic-token", item.id, undefined, fetcher);
    expect(fetcher).toHaveBeenCalledWith(`/workflows/${item.id}`,
      expect.objectContaining({ headers: { Authorization: "Bearer synthetic-token" } }));
  });

  it("submits a transition with the displayed optimistic version", async () => {
    const detail = { ...item, status: "running", attempts: 1, version: 1, events: [
      { status: "pending", occurred_at: item.created_at, attempt: 0,
        actor_id: "synthetic-operator" },
      { status: "running", occurred_at: item.updated_at, attempt: 1,
        actor_id: "synthetic-operator" },
    ] };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(
      JSON.stringify(detail), { status: 200 },
    ));
    await transitionWorkflow("synthetic-token", item.id, "running", 0, undefined, fetcher);
    expect(fetcher).toHaveBeenCalledWith(`/workflows/${item.id}/transitions`,
      expect.objectContaining({
        method: "POST",
        headers: {
          Authorization: "Bearer synthetic-token", "Content-Type": "application/json",
        },
        body: JSON.stringify({ status: "running", expected_version: 0 }),
      }));
  });
});
