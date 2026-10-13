import unittest
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from flowledger.api import create_app
from flowledger.database import Base
from flowledger.repository import SqlAlchemyRepository, build_session_factory
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


class ApiTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        self.repository = SqlAlchemyRepository(build_session_factory(engine))
        self.client = TestClient(create_app(self.repository))
        self.addCleanup(self.client.close)

    def create(self, **kwargs):
        response = self.client.post('/workflows', json={'name': ' synthetic report ', **kwargs})
        self.assertEqual(response.status_code, 201)
        return response

    def transition(self, path, status, expected_version=None):
        if expected_version is None:
            expected_version = self.client.get(path).json()['version']
        return self.client.post(
            path + '/transitions',
            json={'status': status, 'expected_version': expected_version},
        )

    def test_create_and_read_contract(self):
        response = self.create()
        workflow = response.json()
        UUID(workflow['id'])
        self.assertEqual(workflow['name'], 'synthetic report')
        self.assertEqual(workflow['status'], 'pending')
        self.assertEqual(workflow['attempts'], 0)
        self.assertEqual(workflow['max_attempts'], 3)
        self.assertEqual(workflow['version'], 0)
        self.assertEqual(len(workflow['events']), 1)
        self.assertEqual(workflow['events'][0]['attempt'], 0)
        self.assertTrue(workflow['events'][0]['occurred_at'].endswith('Z'))
        self.assertEqual(self.client.get(response.headers['Location']).json(), workflow)

    def test_validation_rejects_bad_configuration(self):
        for body in ({}, {'name': ' '}, {'name': 12}, {'name': 'x' * 201},
                     *({'name': 'x', 'max_attempts': v} for v in (0, -1, True, '3', 1.5, 101)),
                     {'name': 'x', 'unknown': True}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post('/workflows', json=body).status_code, 422)

    def test_missing_and_malformed_ids(self):
        for identifier, code in ((str(uuid4()), 404), ('not-a-uuid', 422)):
            path = f'/workflows/{identifier}'
            self.assertEqual(self.client.get(path).status_code, code)
            self.assertEqual(self.client.post(path + '/transitions',
                json={'status': 'running', 'expected_version': 0}).status_code, code)

    def test_invalid_transition_does_not_change_history(self):
        path = self.create().headers['Location']
        before = self.client.get(path).json()
        response = self.transition(path, 'succeeded')
        self.assertEqual(response.status_code, 409)
        self.assertIn('pending to succeeded', response.json()['detail'])
        self.assertEqual(self.client.get(path).json(), before)
        for body in ({'status': 'unknown', 'expected_version': 0}, {},
                     {'status': 'running', 'expected_version': 0, 'extra': 1},
                     {'status': 'running', 'expected_version': -1}):
            self.assertEqual(self.client.post(path + '/transitions', json=body).status_code, 422)

    def test_retry_exhaustion_and_cancellation(self):
        path = self.create(max_attempts=2).headers['Location']
        for status in ('running', 'failed', 'pending', 'running', 'failed'):
            response = self.transition(path, status)
            self.assertEqual(response.status_code, 200)
        before = response.json()
        self.assertEqual(before['attempts'], 2)
        self.assertEqual(len(before['events']), 6)
        response = self.transition(path, 'pending')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail'], 'Retry limit reached')
        self.assertEqual(self.client.get(path).json(), before)
        self.assertEqual(self.transition(path, 'cancelled').json()['status'], 'cancelled')

    def test_success_is_terminal(self):
        path = self.create().headers['Location']
        for status in ('running', 'succeeded'):
            self.assertEqual(self.transition(path, status).status_code, 200)
        self.assertEqual(self.transition(path, 'running').status_code, 409)

    def test_stale_transition_returns_conflict_without_changing_history(self):
        path = self.create().headers['Location']
        self.assertEqual(self.transition(path, 'running', expected_version=0).status_code, 200)
        response = self.transition(path, 'cancelled', expected_version=0)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail'], 'Expected version 0, found 1')
        restored = self.client.get(path).json()
        self.assertEqual(restored['status'], 'running')
        self.assertEqual(restored['version'], 1)
        self.assertEqual(len(restored['events']), 2)

    def test_application_instances_share_durable_storage(self):
        path = self.create().headers['Location']
        with TestClient(create_app(self.repository)) as other:
            self.assertEqual(other.get(path).status_code, 200)
