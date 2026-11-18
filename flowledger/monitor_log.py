"""Bounded offline inspection for versioned monitor JSONL."""
from argparse import ArgumentParser
from collections.abc import Iterable, Mapping
from importlib.resources import files
from importlib.resources.abc import Traversable
import json
from pathlib import Path
import sys
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


MAX_INPUT_BYTES = 1_000_000
MAX_RECORDS = 10_000
SCHEMA_PATHS = {
    1: files("flowledger.schemas").joinpath("monitor-event-v1.schema.json"),
}
ALERT_NAMES = ("oldest_outbox_age", "terminal_queue_growth")
EVENT_NAMES = ("sample", "sample_error", "stopped")
STOP_REASONS = ("signal", "max_samples", "consecutive_sample_errors")


class MonitorLogError(ValueError):
    """The input cannot be safely interpreted as a supported monitor log."""


def load_validators(
    paths: Mapping[int, Traversable] = SCHEMA_PATHS,
) -> dict[int, Draft202012Validator]:
    """Load and check the explicitly supported schema registry."""
    validators = {}
    for version, path in paths.items():
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validators[version] = Draft202012Validator(
            schema, format_checker=FormatChecker()
        )
    return validators


def _read_lines(path: Path) -> list[str]:
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise MonitorLogError(f"input must not exceed {MAX_INPUT_BYTES} bytes")
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MonitorLogError("input must be UTF-8 JSONL") from error
    except OSError as error:
        raise MonitorLogError("input could not be read") from error
    lines = text.splitlines()
    if not lines:
        raise MonitorLogError("input must contain at least one record")
    if len(lines) > MAX_RECORDS:
        raise MonitorLogError(f"input must not exceed {MAX_RECORDS} records")
    if any(not line.strip() for line in lines):
        raise MonitorLogError("blank JSONL records are not allowed")
    return lines


def _parse_record(line: str, number: int,
                  validators: Mapping[int, Draft202012Validator]) -> dict[str, Any]:
    try:
        record = json.loads(line)
    except json.JSONDecodeError as error:
        raise MonitorLogError(f"record {number} is not valid JSON") from error
    if not isinstance(record, dict):
        raise MonitorLogError(f"record {number} must be an object")
    version = record.get("schema_version")
    if type(version) is not int:
        raise MonitorLogError(f"record {number} has a missing or invalid schema_version")
    validator = validators.get(version)
    if validator is None:
        raise MonitorLogError(f"record {number} uses unsupported schema_version {version}")
    if next(validator.iter_errors(record), None) is not None:
        raise MonitorLogError(f"record {number} does not match schema_version {version}")
    return record


def inspect_log(
    path: Path,
    validators: Mapping[int, Draft202012Validator] | None = None,
) -> dict[str, Any]:
    """Validate a bounded JSONL file and return a payload-free incident summary."""
    validators = load_validators() if validators is None else validators
    records = [
        _parse_record(line, number, validators)
        for number, line in enumerate(_read_lines(path), start=1)
    ]
    event_counts = {name: 0 for name in EVENT_NAMES}
    stop_reasons = {name: 0 for name in STOP_REASONS}
    transitions = {
        name: {"fired": 0, "recovered": 0} for name in ALERT_NAMES
    }
    latest_status = {name: "unknown" for name in ALERT_NAMES}
    observed_at = []
    versions = set()

    for record in records:
        versions.add(record["schema_version"])
        event = record["event"]
        event_counts[event] += 1
        if event in ("sample", "sample_error"):
            observed_at.append(record["observed_at"])
        if event == "sample":
            for alert in record["alerts"]:
                name = alert["name"]
                latest_status[name] = alert["status"]
                transition = alert["transition"]
                if transition in ("fired", "recovered"):
                    transitions[name][transition] += 1
        elif event == "stopped":
            stop_reasons[record["reason"]] += 1

    return {
        "event_counts": event_counts,
        "latest_status": latest_status,
        "observation_window": {
            "first": observed_at[0] if observed_at else None,
            "last": observed_at[-1] if observed_at else None,
        },
        "records": len(records),
        "schema_versions": sorted(versions),
        "stop_reasons": stop_reasons,
        "transitions": transitions,
    }


def _parser() -> ArgumentParser:
    parser = ArgumentParser(
        description="Validate and summarize a bounded FlowLedger monitor JSONL file"
    )
    parser.add_argument("input", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = inspect_log(args.input)
    except MonitorLogError as error:
        print(f"monitor log rejected: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
