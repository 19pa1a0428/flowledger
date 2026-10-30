export const WORKFLOW_STATUSES = [
  "pending",
  "running",
  "succeeded",
  "failed",
  "cancelled",
] as const;

export type WorkflowStatus = (typeof WORKFLOW_STATUSES)[number];

export interface WorkflowSummary {
  id: string;
  name: string;
  owner_id: string;
  status: WorkflowStatus;
  attempts: number;
  max_attempts: number;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface WorkflowPage {
  items: WorkflowSummary[];
  next_cursor: string | null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(record: Record<string, unknown>, field: string): string {
  const value = record[field];
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`Invalid workflow response: ${field}`);
  }
  return value;
}

function requiredInteger(record: Record<string, unknown>, field: string): number {
  const value = record[field];
  if (!Number.isInteger(value) || (value as number) < 0) {
    throw new Error(`Invalid workflow response: ${field}`);
  }
  return value as number;
}

function parseSummary(value: unknown): WorkflowSummary {
  if (!isRecord(value)) throw new Error("Invalid workflow response: item");
  const id = requiredString(value, "id");
  const status = requiredString(value, "status");
  const createdAt = requiredString(value, "created_at");
  const updatedAt = requiredString(value, "updated_at");
  if (!UUID.test(id) || !WORKFLOW_STATUSES.includes(status as WorkflowStatus)) {
    throw new Error("Invalid workflow response: identity or status");
  }
  if (Number.isNaN(Date.parse(createdAt)) || Number.isNaN(Date.parse(updatedAt))) {
    throw new Error("Invalid workflow response: timestamp");
  }
  return {
    id,
    name: requiredString(value, "name"),
    owner_id: requiredString(value, "owner_id"),
    status: status as WorkflowStatus,
    attempts: requiredInteger(value, "attempts"),
    max_attempts: requiredInteger(value, "max_attempts"),
    version: requiredInteger(value, "version"),
    created_at: createdAt,
    updated_at: updatedAt,
  };
}

export function parseWorkflowPage(value: unknown): WorkflowPage {
  if (!isRecord(value) || !Array.isArray(value.items)) {
    throw new Error("Invalid workflow response: page");
  }
  const cursor = value.next_cursor;
  if (cursor !== null && (typeof cursor !== "string" || !UUID.test(cursor))) {
    throw new Error("Invalid workflow response: next_cursor");
  }
  return { items: value.items.map(parseSummary), next_cursor: cursor };
}

interface FetchWorkflowOptions {
  token: string;
  cursor?: string | null;
  status?: WorkflowStatus | "";
  signal?: AbortSignal;
  fetcher?: typeof fetch;
}

export async function fetchWorkflows({
  token,
  cursor,
  status,
  signal,
  fetcher = fetch,
}: FetchWorkflowOptions): Promise<WorkflowPage> {
  const parameters = new URLSearchParams({ limit: "10" });
  if (cursor) parameters.set("cursor", cursor);
  if (status) parameters.set("status", status);
  const response = await fetcher(`/workflows?${parameters}`, {
    headers: { Authorization: `Bearer ${token}` },
    signal,
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body: unknown = await response.json();
      if (isRecord(body) && typeof body.detail === "string") message = body.detail;
    } catch {
      // Preserve the status-based fallback when the server did not return JSON.
    }
    throw new ApiError(message, response.status);
  }
  return parseWorkflowPage(await response.json());
}
