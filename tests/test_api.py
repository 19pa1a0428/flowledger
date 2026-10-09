import unittest
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from flowledger.api import create_app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app())
        self.addCleanup(self.client.close)

    def create(self, **kwargs):
        response = self.client.post('/workflows', json={'name': ' synthetic report ', **kwargs})
        self.assertEqual(response.status_code, 201)
        return response

    def test_create_and_read_contract(self):
        response = self.create()
        workflow = response.json()
        UUID(workflow['id'])
        self.assertEqual(workflow['name'], 'synthetic report')
        self.assertEqual(workflow['status'], 'pending')
        self.assertEqual(workflow['attempts'], 0)
        self.assertEqual(workflow['max_attempts'], 3)
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
            self.assertEqual(self.client.post(path + '/transitions', json={'status': 'running'}).status_code, code)

    def test_invalid_transition_does_not_change_history(self):
        path = self.create().headers['Location']
        before = self.client.get(path).json()
        response = self.client.post(path + '/transitions', json={'status': 'succeeded'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('pending to succeeded', response.json()['detail'])
        self.assertEqual(self.client.get(path).json(), before)
        for body in ({'status': 'unknown'}, {}, {'status': 'running', 'extra': 1}):
            self.assertEqual(self.client.post(path + '/transitions', json=body).status_code, 422)

    def test_retry_exhaustion_and_cancellation(self):
        path = self.create(max_attempts=2).headers['Location']
        for status in ('running', 'failed', 'pending', 'running', 'failed'):
            response = self.client.post(path + '/transitions', json={'status': status})
            self.assertEqual(response.status_code, 200)
        before = response.json()
        self.assertEqual(before['attempts'], 2)
        self.assertEqual(len(before['events']), 6)
        response = self.client.post(path + '/transitions', json={'status': 'pending'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail'], 'Retry limit reached')
        self.assertEqual(self.client.get(path).json(), before)
        self.assertEqual(self.client.post(path + '/transitions', json={'status': 'cancelled'}).json()['status'], 'cancelled')

    def test_success_is_terminal(self):
        path = self.create().headers['Location']
        for status in ('running', 'succeeded'):
            self.assertEqual(self.client.post(path + '/transitions', json={'status': status}).status_code, 200)
        self.assertEqual(self.client.post(path + '/transitions', json={'status': 'running'}).status_code, 409)

    def test_application_instances_have_separate_temporary_storage(self):
        path = self.create().headers['Location']
        with TestClient(create_app()) as other:
            self.assertEqual(other.get(path).status_code, 404)
