"""Run the deterministic, synthetic FlowLedger portfolio walkthrough."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TOKEN = "synthetic-local-operator-token-0000000000000000"

RequestFunction = Callable[..., tuple[int, dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class DemoResult:
    workflow_id: str
    final_status: str
    attempts: int
    version: int
    event_statuses: tuple[str, ...]


class HttpClient:
    """Small JSON client that keeps the demo independent of test-only packages."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, path: str, *, method: str = "GET",
                body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            try:
                payload = json.loads(error.read())
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = {"detail": "Non-JSON error response"}
            return error.code, payload


def _expect(status: int, payload: dict[str, Any], expected: int, step: str) -> dict[str, Any]:
    if status != expected:
        detail = payload.get("detail", "unknown error")
        raise RuntimeError(f"{step} returned HTTP {status}: {detail}")
    return payload


def _transition(request: RequestFunction, workflow: dict[str, Any], target: str,
                *, expected_status: int = 200) -> dict[str, Any]:
    status, payload = request(
        f"/workflows/{workflow['id']}/transitions",
        method="POST",
        body={"status": target, "expected_version": workflow["version"]},
    )
    return _expect(status, payload, expected_status, f"transition to {target}")


def run_demo(request: RequestFunction, *, timeout: float = 20.0,
             clock: Callable[[], float] = time.monotonic,
             sleep: Callable[[float], None] = time.sleep) -> DemoResult:
    """Exercise delivery, conflict handling, retry and terminal completion."""
    status, payload = request("/health/ready")
    _expect(status, payload, 200, "readiness check")

    status, workflow = request(
        "/workflows",
        method="POST",
        body={"name": "synthetic portfolio walkthrough", "max_attempts": 2},
    )
    workflow = _expect(status, workflow, 201, "workflow creation")
    workflow_id = workflow.get("id")
    if not isinstance(workflow_id, str):
        raise RuntimeError("workflow creation returned no string id")

    deadline = clock() + timeout
    while workflow.get("status") != "running":
        if clock() >= deadline:
            raise RuntimeError("worker did not move the synthetic workflow to running")
        sleep(0.25)
        status, workflow = request(f"/workflows/{workflow_id}")
        workflow = _expect(status, workflow, 200, "workflow polling")

    stale_running = workflow
    workflow = _transition(request, workflow, "failed")

    status, conflict = request(
        f"/workflows/{workflow_id}/transitions",
        method="POST",
        body={"status": "succeeded", "expected_version": stale_running["version"]},
    )
    _expect(status, conflict, 409, "stale transition proof")

    workflow = _transition(request, workflow, "pending")
    workflow = _transition(request, workflow, "running")
    workflow = _transition(request, workflow, "succeeded")

    status, final = request(f"/workflows/{workflow_id}")
    final = _expect(status, final, 200, "final workflow read")
    event_statuses = tuple(event.get("status") for event in final.get("events", []))
    expected_history = (
        "pending", "running", "failed", "pending", "running", "succeeded",
    )
    if (final.get("status"), final.get("attempts"), final.get("version"), event_statuses) != (
        "succeeded", 2, 5, expected_history,
    ):
        raise RuntimeError("final workflow did not match the expected retry history")

    return DemoResult(
        workflow_id=workflow_id,
        final_status=final["status"],
        attempts=final["attempts"],
        version=final["version"],
        event_statuses=event_statuses,
    )


def main() -> int:
    client = HttpClient(
        os.getenv("FLOWLEDGER_DEMO_URL", DEFAULT_BASE_URL),
        os.getenv("FLOWLEDGER_DEMO_TOKEN", DEFAULT_TOKEN),
    )
    result = run_demo(client.request)
    print(f"Workflow: {result.workflow_id}")
    print("History: " + " -> ".join(result.event_statuses))
    print(
        f"Demo passed: {result.final_status}, {result.attempts} attempts, "
        f"version {result.version}; stale write rejected"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
