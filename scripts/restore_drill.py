"""Destructive restore proof for the disposable Compose project."""
from argparse import ArgumentParser
import subprocess
import time

from scripts.compose_smoke import request


COMPOSE = ("docker", "compose", "--env-file", ".env.example")


def compose(*args: str, input_bytes: bytes | None = None,
            capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        (*COMPOSE, *args), input=input_bytes, check=True,
        stdout=subprocess.PIPE if capture else None,
    )


def drill(*, confirm_disposable: bool) -> None:
    if not confirm_disposable:
        raise ValueError("Restore drill requires --confirm-disposable")

    status, workflow = request(
        "/workflows", method="POST",
        body={"name": "synthetic restore drill", "max_attempts": 2},
    )
    if status != 201:
        raise RuntimeError(f"unexpected create status: {status}")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        _, current = request(f"/workflows/{workflow['id']}")
        if current["status"] == "running":
            break
        time.sleep(0.25)
    else:
        raise RuntimeError("workflow did not reach running before backup")

    dump = compose(
        "exec", "-T", "postgres", "pg_dump", "--username", "flowledger",
        "--dbname", "flowledger", "--format", "custom", capture=True,
    ).stdout
    if not dump:
        raise RuntimeError("pg_dump produced an empty backup")

    compose("down", "--volumes")
    compose("up", "--detach", "--wait", "postgres")
    compose(
        "exec", "-T", "postgres", "pg_restore", "--username", "flowledger",
        "--dbname", "flowledger", "--clean", "--if-exists", input_bytes=dump,
    )
    compose("up", "--detach", "--wait")

    _, restored = request(f"/workflows/{workflow['id']}")
    if restored["status"] != "running" or len(restored["events"]) != 2:
        raise RuntimeError("restored workflow snapshot/history do not match the backup")
    print("Restore drill passed: snapshot and two-event history recovered")


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser()
    parser.add_argument("--confirm-disposable", action="store_true")
    args = parser.parse_args(argv)
    drill(confirm_disposable=args.confirm_disposable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
