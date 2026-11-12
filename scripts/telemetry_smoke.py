"""Prove synthetic traces and metrics reach the optional local Collector."""
from collections.abc import Callable
import re
import subprocess
import time

if __package__:
    from scripts.compose_smoke import request
else:
    # ``python scripts/telemetry_smoke.py`` places scripts/, not the repository
    # root, on sys.path. Keep the documented and CI entry point importable while
    # retaining package imports for unit tests.
    from compose_smoke import request


COMPOSE = (
    "docker", "compose", "--env-file", ".env.example",
    "-f", "compose.yaml", "-f", "compose.otel.yaml", "--profile", "telemetry",
)
EVIDENCE = (
    "flowledger-api",
    "HTTP POST /workflows",
    "flowledger-worker",
    "worker.execute",
    "flowledger.relay.commands",
    "flowledger.worker.commands",
    "flowledger.queue.depth",
)
CORRELATION_EVIDENCE = "shared API/worker trace ID"


def collector_logs() -> str:
    result = subprocess.run(
        (*COMPOSE, "logs", "--no-color", "otel-collector"),
        check=True, capture_output=True, text=True,
    )
    return result.stdout + result.stderr


def _trace_ids(logs: str, span_name: str) -> set[str]:
    trace_ids = set()
    for block in re.split(r"(?=Span #\d+)", logs):
        # Compose prefixes every container log line (``service |``), while a
        # captured fixture or direct Collector log does not. Match the fields
        # themselves so both representations prove the same correlation.
        name = re.search(r"\bName\s*:\s*(.+?)\s*$", block, re.MULTILINE)
        trace_id = re.search(r"\bTrace ID\s*:\s*([0-9a-fA-F]+)\s*$",
                             block, re.MULTILINE)
        if name is not None and trace_id is not None and name.group(1) == span_name:
            trace_ids.add(trace_id.group(1).lower())
    return trace_ids


def missing_evidence(logs: str) -> tuple[str, ...]:
    """Return only fixed signal names, never synthetic payload contents."""
    missing = [item for item in EVIDENCE if item not in logs]
    api_traces = _trace_ids(logs, "HTTP POST /workflows")
    worker_traces = _trace_ids(logs, "worker.execute")
    if not api_traces.intersection(worker_traces):
        missing.append(CORRELATION_EVIDENCE)
    return tuple(missing)


def smoke(*, request_fn: Callable = request,
          logs_fn: Callable[[], str] = collector_logs,
          attempts: int = 120,
          pause: Callable[[float], None] = time.sleep) -> None:
    if type(attempts) is not int or not 1 <= attempts <= 1_000:
        raise ValueError("attempts must be an integer between 1 and 1000")
    status, workflow = request_fn(
        "/workflows", method="POST",
        body={"name": "synthetic telemetry smoke", "max_attempts": 2},
    )
    if status != 201:
        raise RuntimeError(f"unexpected create status: {status}")

    for _ in range(attempts):
        _, current = request_fn(f"/workflows/{workflow['id']}")
        if current["status"] == "running":
            break
        pause(0.25)
    else:
        raise RuntimeError("worker did not process the synthetic telemetry command")

    missing = (*EVIDENCE, CORRELATION_EVIDENCE)
    for _ in range(attempts):
        missing = missing_evidence(logs_fn())
        if not missing:
            print("Telemetry smoke passed: Collector received traces and metrics")
            return
        pause(0.25)
    raise RuntimeError("Collector evidence missing: " + ", ".join(missing))


def main() -> int:
    smoke()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
