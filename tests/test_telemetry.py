import tempfile
import unittest
from pathlib import Path

from fakeredis import FakeRedis
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine

from flowledger.api import create_app
from flowledger.auth import Principal, Role, TokenAuthenticator
from flowledger.database import Base, OutboxRecord
from flowledger.jobs import RedisSubmissionQueue, WorkerCommand
from flowledger.outbox import relay_pending
from flowledger.repository import SqlAlchemyRepository, build_session_factory
from flowledger.telemetry import configure_telemetry
from flowledger.claims import RedisClaimQueue
from flowledger.worker import run_once


class TelemetryTests(unittest.TestCase):
    token = "telemetry-operator-token-000000000000"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = create_engine(
            f"sqlite:///{Path(self.temp.name) / 'telemetry.db'}"
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = build_session_factory(self.engine)
        self.repository = SqlAlchemyRepository(self.sessions)
        self.redis = FakeRedis()
        self.exporter = InMemorySpanExporter()
        provider = TracerProvider(sampler=ALWAYS_ON)
        provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = provider.get_tracer("flowledger.contract-test")
        authenticator = TokenAuthenticator({
            self.token: Principal("synthetic-operator", Role.OPERATOR),
        })
        self.client = TestClient(
            create_app(self.repository, authenticator, self.tracer),
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.addCleanup(self.client.close)

    def test_api_context_survives_outbox_and_parents_worker_span(self):
        response = self.client.post("/workflows", json={"name": "synthetic trace"})
        self.assertEqual(response.status_code, 201)
        workflow_id = response.json()["id"]

        with self.sessions() as session:
            row = session.query(OutboxRecord).one()
            stored = WorkerCommand.model_validate_json(row.payload)
        self.assertIsNotNone(stored.traceparent)

        submissions = RedisSubmissionQueue(self.redis)
        self.assertEqual(relay_pending(self.sessions, submissions), 1)
        queued_payload = self.redis.lindex("flowledger:{jobs}:pending", 0).decode()
        self.assertEqual(queued_payload, row.payload)

        self.assertTrue(run_once(
            RedisClaimQueue(self.redis), self.repository, worker_tracer=self.tracer,
        ))
        self.assertEqual(self.repository.get(stored.workflow_id).status.value, "running")

        spans = self.exporter.get_finished_spans()
        request_span = next(span for span in spans if span.name == "HTTP POST /workflows")
        worker_span = next(span for span in spans if span.name == "worker.execute")
        self.assertEqual(worker_span.context.trace_id, request_span.context.trace_id)
        self.assertEqual(worker_span.parent.span_id, request_span.context.span_id)
        self.assertEqual(worker_span.attributes["flowledger.workflow_id"], workflow_id)

    def test_missing_optional_endpoint_disables_export_without_failure(self):
        self.assertFalse(configure_telemetry({"FLOWLEDGER_OTEL_ENABLED": "true"}))


if __name__ == "__main__":
    unittest.main()
