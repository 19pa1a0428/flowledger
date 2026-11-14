"""Deterministic alert-policy evaluation over synthetic operational samples."""
from argparse import ArgumentParser
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
from typing import Any, Iterable, Sequence


MAX_INPUT_BYTES = 1_000_000
MAX_SAMPLES = 1_000


class AlertStatus(str, Enum):
    CLEAR = "clear"
    FIRING = "firing"


class AlertTransition(str, Enum):
    NONE = "none"
    FIRED = "fired"
    RECOVERED = "recovered"


@dataclass(frozen=True)
class AlertPolicy:
    outbox_age_seconds: int = 300
    terminal_growth: int = 1
    trigger_samples: int = 3
    recovery_samples: int = 2

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if type(value) is not int or value < 1 or value > 86_400:
                raise ValueError(f"{name} must be an integer between 1 and 86400")


@dataclass(frozen=True)
class AlertSample:
    observed_at: datetime
    oldest_outbox_age_seconds: int | None
    quarantined: int
    exhausted: int

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        if (self.oldest_outbox_age_seconds is not None and
                (type(self.oldest_outbox_age_seconds) is not int or
                 self.oldest_outbox_age_seconds < 0)):
            raise ValueError("oldest_outbox_age_seconds must be null or nonnegative")
        for name in ("quarantined", "exhausted"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")


@dataclass(frozen=True)
class AlertDecision:
    name: str
    status: AlertStatus
    transition: AlertTransition
    breach_streak: int
    recovery_streak: int


@dataclass(frozen=True)
class SampleEvaluation:
    observed_at: str
    terminal_growth: int
    alerts: tuple[AlertDecision, AlertDecision]


@dataclass
class _AlertTracker:
    status: AlertStatus = AlertStatus.CLEAR
    breach_streak: int = 0
    recovery_streak: int = 0

    def update(self, name: str, breached: bool, policy: AlertPolicy) -> AlertDecision:
        transition = AlertTransition.NONE
        if self.status is AlertStatus.CLEAR:
            self.recovery_streak = 0
            self.breach_streak = self.breach_streak + 1 if breached else 0
            if self.breach_streak >= policy.trigger_samples:
                self.status = AlertStatus.FIRING
                self.breach_streak = 0
                transition = AlertTransition.FIRED
        else:
            self.breach_streak = 0
            self.recovery_streak = 0 if breached else self.recovery_streak + 1
            if self.recovery_streak >= policy.recovery_samples:
                self.status = AlertStatus.CLEAR
                self.recovery_streak = 0
                transition = AlertTransition.RECOVERED
        return AlertDecision(name, self.status, transition,
                             self.breach_streak, self.recovery_streak)


class AlertPolicyEvaluator:
    """Retain alert state across ordered samples and explicit observation gaps."""

    def __init__(self, policy: AlertPolicy):
        self.policy = policy
        self._outbox = _AlertTracker()
        self._terminal = _AlertTracker()
        self._previous: AlertSample | None = None

    def update(self, sample: AlertSample) -> SampleEvaluation:
        if self._previous is not None and sample.observed_at <= self._previous.observed_at:
            raise ValueError("observed_at values must be strictly increasing")
        growth = 0 if self._previous is None else max(
            0,
            sample.quarantined + sample.exhausted -
            self._previous.quarantined - self._previous.exhausted,
        )
        outbox_breached = (
            sample.oldest_outbox_age_seconds is not None and
            sample.oldest_outbox_age_seconds >= self.policy.outbox_age_seconds
        )
        alerts = (
            self._outbox.update("oldest_outbox_age", outbox_breached, self.policy),
            self._terminal.update("terminal_queue_growth",
                                  growth >= self.policy.terminal_growth,
                                  self.policy),
        )
        self._previous = sample
        return SampleEvaluation(sample.observed_at.isoformat(), growth, alerts)


def evaluate(samples: Sequence[AlertSample], policy: AlertPolicy) -> tuple[SampleEvaluation, ...]:
    """Evaluate ordered samples without reading services or sending notifications."""
    if not samples:
        raise ValueError("at least one sample is required")
    if len(samples) > MAX_SAMPLES:
        raise ValueError(f"at most {MAX_SAMPLES} samples are allowed")

    evaluator = AlertPolicyEvaluator(policy)
    return tuple(evaluator.update(sample) for sample in samples)


def _integer(data: dict[str, Any], name: str) -> int:
    value = data.get(name)
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def parse_samples(value: Any) -> tuple[AlertSample, ...]:
    if not isinstance(value, list):
        raise ValueError("input must be a JSON array")
    if len(value) > MAX_SAMPLES:
        raise ValueError(f"at most {MAX_SAMPLES} samples are allowed")
    parsed = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"sample {index} must be an object")
        try:
            observed_at = datetime.fromisoformat(item["observed_at"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"sample {index} has an invalid observed_at") from error
        oldest = item.get("oldest_outbox_age_seconds")
        parsed.append(AlertSample(observed_at, oldest,
                                  _integer(item, "quarantined"),
                                  _integer(item, "exhausted")))
    return tuple(parsed)


def read_samples(path: Path) -> tuple[AlertSample, ...]:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"input must not exceed {MAX_INPUT_BYTES} bytes")
    return parse_samples(json.loads(path.read_text(encoding="utf-8")))


def _parser() -> ArgumentParser:
    parser = ArgumentParser(description="Evaluate synthetic FlowLedger alert samples")
    parser.add_argument("input", type=Path, help="bounded synthetic JSON sample file")
    parser.add_argument("--outbox-age-seconds", type=int, default=300)
    parser.add_argument("--terminal-growth", type=int, default=1)
    parser.add_argument("--trigger-samples", type=int, default=3)
    parser.add_argument("--recovery-samples", type=int, default=2)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    policy = AlertPolicy(args.outbox_age_seconds, args.terminal_growth,
                         args.trigger_samples, args.recovery_samples)
    results = evaluate(read_samples(args.input), policy)
    print(json.dumps([asdict(result) for result in results],
                     separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
