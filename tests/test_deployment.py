from pathlib import Path
import subprocess
import tempfile
from unittest import TestCase
from unittest.mock import call, patch

from scripts.restore_drill import drill
from scripts.verify_deployment import FILES, verify


class DeploymentTests(TestCase):
    def test_repository_deployment_images_are_immutable(self):
        verify(Path(__file__).resolve().parents[1])

    def test_optional_collector_is_profiled_and_not_host_published(self):
        root = Path(__file__).resolve().parents[1]
        override = (root / "compose.otel.yaml").read_text(encoding="utf-8")
        config = (root / "ops/otel-collector.yaml").read_text(encoding="utf-8")

        self.assertIn('profiles: ["telemetry"]', override)
        self.assertNotIn("ports:", override)
        self.assertIn("http://otel-collector:4318/v1/traces", override)
        self.assertIn("http://otel-collector:4318/v1/metrics", override)
        self.assertIn("verbosity: detailed", config)

    def test_unpinned_image_is_rejected(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory)
            for name in FILES:
                target = copy / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text((root / name).read_text(encoding="utf-8"), encoding="utf-8")
            compose = copy / "compose.yaml"
            compose.write_text(
                compose.read_text(encoding="utf-8").replace(
                    "redis:7.4-alpine@sha256:", "redis:7.4-alpine # sha256:"
                ), encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "reviewed image pin|Unpinned"):
                verify(copy)

    def test_required_os_security_patch_is_explicit(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory)
            for name in FILES:
                target = copy / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text((root / name).read_text(encoding="utf-8"), encoding="utf-8")
            dockerfile = copy / "Dockerfile"
            dockerfile.write_text(
                dockerfile.read_text(encoding="utf-8").replace(
                    "zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1", "zlib1g"
                ), encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Missing reviewed OS security patch"):
                verify(copy)

    def test_restore_drill_requires_destructive_confirmation(self):
        with self.assertRaisesRegex(ValueError, "confirm-disposable"):
            drill(confirm_disposable=False)

    @patch("scripts.restore_drill.compose")
    @patch("scripts.restore_drill.request")
    def test_restore_drill_recreates_volumes_and_verifies_history(self, request, compose):
        workflow = {"id": "00000000-0000-0000-0000-000000000001"}
        running = {**workflow, "status": "running", "events": [{}, {}]}
        request.side_effect = [(201, workflow), (200, running), (200, running)]
        compose.side_effect = [
            subprocess.CompletedProcess([], 0, stdout=b"synthetic-dump"),
            subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0),
        ]

        drill(confirm_disposable=True)

        self.assertEqual(compose.call_args_list[1], call("down", "--volumes"))
        self.assertEqual(compose.call_args_list[2],
                         call("up", "--detach", "--wait", "postgres"))
        self.assertEqual(compose.call_args_list[3].kwargs["input_bytes"], b"synthetic-dump")
        self.assertEqual(compose.call_args_list[4], call("up", "--detach", "--wait"))
