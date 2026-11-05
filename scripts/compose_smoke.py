"""Exercise API -> outbox -> Redis -> worker through the Compose stack."""
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE_URL = "http://127.0.0.1:8000"
TOKEN = "synthetic-local-operator-token-0000000000000000"


def request(path: str, *, method: str = "GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(BASE_URL + path, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    })
    with urlopen(req, timeout=3) as response:
        return response.status, json.load(response)


def main() -> int:
    status, workflow = request(
        "/workflows", method="POST",
        body={"name": "synthetic compose smoke", "max_attempts": 2},
    )
    if status != 201:
        raise RuntimeError(f"unexpected create status: {status}")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        _, current = request(f"/workflows/{workflow['id']}")
        if current["status"] == "running":
            print("Compose smoke passed: workflow reached running")
            return 0
        time.sleep(0.25)
    raise RuntimeError("worker did not move the synthetic workflow to running")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HTTPError as error:
        raise SystemExit(f"HTTP {error.code}: {error.read().decode()}") from error
