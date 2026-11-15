"""Prove the optional monitor profile with disposable synthetic Redis state."""
from collections.abc import Sequence
from dataclasses import dataclass
import json
from queue import Empty, Queue
import subprocess
from threading import Thread
from typing import Any


COMPOSE = (
    "docker", "compose", "--env-file", ".env.example", "--profile", "operations",
)
QUARANTINE_KEY = "flowledger:{jobs}:quarantine"
MARKERS = ("flowledger-monitor-smoke-one", "flowledger-monitor-smoke-two")


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run((*COMPOSE, *args), check=check, capture_output=True, text=True)


def _marker(marker: str, *, add: bool) -> None:
    redis_args = ("RPUSH", QUARANTINE_KEY, marker) if add else (
        "LREM", QUARANTINE_KEY, "0", marker,
    )
    _compose("exec", "-T", "redis", "redis-cli", *redis_args)


def _cleanup() -> None:
    for marker in MARKERS:
        _marker(marker, add=False)


@dataclass
class MonitorProcess:
    process: subprocess.Popen[str]
    lines: Queue[str | None]


def _capture(stream: Any, lines: Queue[str | None]) -> None:
    try:
        for line in stream:
            lines.put(line)
    finally:
        lines.put(None)


def _start_monitor(*environment: str) -> MonitorProcess:
    command = [*COMPOSE, "run", "--rm", "--no-deps", "-T"]
    for value in environment:
        command.extend(("-e", value))
    command.append("monitor")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    if process.stdout is None:
        raise RuntimeError("monitor stdout was not captured")
    lines: Queue[str | None] = Queue()
    Thread(target=_capture, args=(process.stdout, lines), daemon=True).start()
    return MonitorProcess(process, lines)


def _next_event(running: MonitorProcess, timeout: float = 20) -> dict[str, Any]:
    try:
        line = running.lines.get(timeout=timeout)
    except Empty as error:
        raise RuntimeError("timed out waiting for monitor output") from error
    if line is None:
        stderr = (running.process.stderr.read()
                  if running.process.stderr is not None else "")
        raise RuntimeError(f"monitor exited before evidence: {stderr[-500:]}")
    try:
        event = json.loads(line)
    except json.JSONDecodeError as error:
        raise RuntimeError("monitor emitted non-JSON output") from error
    if not isinstance(event, dict):
        raise RuntimeError("monitor event must be an object")
    return event


def _sample(process: MonitorProcess) -> dict[str, Any]:
    event = _next_event(process)
    if event.get("event") != "sample":
        raise RuntimeError(f"expected sample event, received {event.get('event')!r}")
    return event


def verify_incident(samples: Sequence[dict[str, Any]]) -> None:
    if len(samples) != 5:
        raise RuntimeError("expected exactly five monitor samples")
    terminal = []
    for sample in samples:
        alerts = sample.get("alerts")
        if not isinstance(alerts, list) or len(alerts) != 2:
            raise RuntimeError("monitor sample has an invalid alert set")
        terminal.append(alerts[1])
    if [sample.get("terminal_growth") for sample in samples] != [0, 1, 1, 0, 0]:
        raise RuntimeError("terminal growth evidence did not match the synthetic incident")
    if terminal[2].get("transition") != "fired":
        raise RuntimeError("terminal queue alert did not fire")
    if terminal[4].get("transition") != "recovered":
        raise RuntimeError("terminal queue alert did not recover")


def verify_capped_failure(events: Sequence[dict[str, Any]], return_code: int) -> None:
    if return_code != 1:
        raise RuntimeError("capped terminal counts must stop the monitor with status 1")
    if len(events) != 2:
        raise RuntimeError("expected one sample error and one stop event")
    if (events[0].get("event"), events[0].get("error_type")) != (
            "sample_error", "CappedTerminalCounts"):
        raise RuntimeError("capped terminal counts were not reported explicitly")
    if (events[1].get("event"), events[1].get("reason")) != (
            "stopped", "consecutive_sample_errors"):
        raise RuntimeError("capped-count failure did not use the bounded error stop")


def _finish(running: MonitorProcess, expected_code: int) -> None:
    process = running.process
    try:
        return_code = process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=5)
        raise RuntimeError("monitor did not exit after its sample bound")
    if return_code != expected_code:
        stderr = process.stderr.read() if process.stderr is not None else ""
        raise RuntimeError(f"monitor exited with {return_code}: {stderr[-500:]}")


def _terminate(running: MonitorProcess | None) -> None:
    if running is not None and running.process.poll() is None:
        running.process.terminate()
        try:
            running.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            running.process.kill()
            running.process.wait(timeout=5)


def smoke() -> None:
    _cleanup()
    process = None
    try:
        process = _start_monitor(
            "FLOWLEDGER_MONITOR_INTERVAL_SECONDS=1",
            "FLOWLEDGER_MONITOR_MAX_SAMPLES=5",
            "FLOWLEDGER_ALERT_OUTBOX_AGE_SECONDS=86400",
            "FLOWLEDGER_ALERT_TERMINAL_GROWTH=1",
            "FLOWLEDGER_ALERT_TRIGGER_SAMPLES=2",
            "FLOWLEDGER_ALERT_RECOVERY_SAMPLES=2",
        )
        samples = [_sample(process)]
        _marker(MARKERS[0], add=True)
        samples.append(_sample(process))
        _marker(MARKERS[1], add=True)
        samples.extend((_sample(process), _sample(process), _sample(process)))
        stopped = _next_event(process)
        _finish(process, 0)
        if (stopped.get("event"), stopped.get("reason")) != ("stopped", "max_samples"):
            raise RuntimeError("bounded monitor did not report max_samples completion")
        verify_incident(samples)
    finally:
        _terminate(process)
        _cleanup()

    process = None
    try:
        for marker in MARKERS:
            _marker(marker, add=True)
        process = _start_monitor(
            "FLOWLEDGER_MONITOR_INTERVAL_SECONDS=1",
            "FLOWLEDGER_MONITOR_COUNT_LIMIT=1",
            "FLOWLEDGER_MONITOR_MAX_CONSECUTIVE_ERRORS=1",
        )
        events = (_next_event(process), _next_event(process))
        return_code = process.process.wait(timeout=20)
        verify_capped_failure(events, return_code)
    finally:
        _terminate(process)
        _cleanup()


def main() -> int:
    smoke()
    print("Monitor smoke passed: fired, recovered, and rejected capped counts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
