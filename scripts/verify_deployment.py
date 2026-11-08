"""Fail when deployment base/service images are not the reviewed immutable inputs."""
from pathlib import Path
import re


EXPECTED = {
    "python:3.12-slim": "4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54",
    "postgres:17-alpine": "5d9ee39acd134b44f52fc49ca3b88fc651f0560b0db93292fcd37ff150c71d3a",
    "redis:7.4-alpine": "c5d34a98396a4775e223c74e929162c35c5a2182901ba26b50ade939941ce14b",
}
FILES = ("Dockerfile", "compose.yaml", ".github/workflows/tests.yml")
EXPECTED_OS_PATCHES = (
    "zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1",
)


def verify(root: Path) -> None:
    contents = {name: (root / name).read_text(encoding="utf-8") for name in FILES}
    combined = "\n".join(contents.values())
    for image, digest in EXPECTED.items():
        pinned = f"{image}@sha256:{digest}"
        if pinned not in combined:
            raise ValueError(f"Missing reviewed image pin for {image}")
        pattern = re.compile(rf"{re.escape(image)}(?!@sha256:[0-9a-f]{{64}})")
        for name, content in contents.items():
            if pattern.search(content):
                raise ValueError(f"Unpinned {image} reference in {name}")
    for package in EXPECTED_OS_PATCHES:
        if package not in contents["Dockerfile"]:
            raise ValueError(f"Missing reviewed OS security patch for {package.split('=')[0]}")


if __name__ == "__main__":
    verify(Path(__file__).resolve().parents[1])
    print("Deployment image pins verified")
