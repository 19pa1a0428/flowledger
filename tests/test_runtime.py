import os
from unittest import TestCase
from unittest.mock import patch

from flowledger.runtime import RuntimeConfig, run_relay_loop


class StopAfterWait:
    def __init__(self):
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.waits.append(timeout)
        self.stopped = True
        return True


class RuntimeTests(TestCase):
    def test_relay_drains_full_batches_before_waiting(self):
        stop = StopAfterWait()
        with patch("flowledger.runtime.relay_pending", side_effect=[2, 1]) as relay:
            run_relay_loop(object(), object(), stop, batch_size=2,
                           idle_wait_seconds=0.25)
        self.assertEqual(relay.call_count, 2)
        self.assertEqual(stop.waits, [0.25])

    def test_relay_propagates_infrastructure_failure(self):
        with patch("flowledger.runtime.relay_pending", side_effect=RuntimeError("down")):
            with self.assertRaisesRegex(RuntimeError, "down"):
                run_relay_loop(object(), object(), StopAfterWait())

    def test_environment_configuration_is_bounded(self):
        values = {
            "FLOWLEDGER_DATABASE_URL": "postgresql+psycopg://db/test",
            "FLOWLEDGER_REDIS_URL": "redis://redis/0",
            "FLOWLEDGER_BATCH_SIZE": "12",
            "FLOWLEDGER_IDLE_WAIT_SECONDS": "0.5",
            "FLOWLEDGER_LEASE_MS": "20000",
        }
        with patch.dict(os.environ, values, clear=True):
            self.assertEqual(RuntimeConfig.from_environment().batch_size, 12)
        values["FLOWLEDGER_BATCH_SIZE"] = "0"
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaisesRegex(ValueError, "Batch sizes"):
                RuntimeConfig.from_environment()

    def test_environment_requires_service_urls(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "required"):
                RuntimeConfig.from_environment()
