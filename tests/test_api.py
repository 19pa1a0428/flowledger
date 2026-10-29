import unittest
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from flowledger.api import create_app
from flowledger.auth import Credential, Principal, Role, TokenAuthenticator
from flowledger.database import Base
from flowledger.repository import SqlAlchemyRepository, build_session_factory
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


class ApiTests(unittest.TestCase):
    operator_token = 'operator-test-token-000000000000000'
    viewer_token = 'viewer-test-token-00000000000000000'
    outsider_token = 'outsider-test-token-000000000000000'
    other_operator_token = 'other-operator-token-000000000000000'

    def setUp(self):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        self.repository = SqlAlchemyRepository(build_session_factory(engine))
        self.authenticator = TokenAuthenticator({
            self.operator_token: Principal('synthetic-operator', Role.OPERATOR),
            self.viewer_token: Principal('synthetic-operator', Role.VIEWER),
            self.outsider_token: Principal('synthetic-outsider', Role.VIEWER),
            self.other_operator_token: Principal('other-operator', Role.OPERATOR),
        })
        self.app = create_app(self.repository, self.authenticator)
        self.client = TestClient(self.app, headers={
            'Authorization': f'Bearer {self.operator_token}'
        })
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
        self.assertEqual(workflow['owner_id'], 'synthetic-operator')
        self.assertEqual(workflow['status'], 'pending')
        self.assertEqual(workflow['attempts'], 0)
        self.assertEqual(workflow['max_attempts'], 3)
        self.assertEqual(workflow['version'], 0)
        self.assertEqual(len(workflow['events']), 1)
        self.assertEqual(workflow['events'][0]['attempt'], 0)
        self.assertEqual(workflow['events'][0]['actor_id'], 'synthetic-operator')
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
        with TestClient(create_app(self.repository, self.authenticator), headers={
            'Authorization': f'Bearer {self.operator_token}'
        }) as other:
            self.assertEqual(other.get(path).status_code, 200)

    def test_authentication_and_role_contract(self):
        path = self.create().headers['Location']
        with TestClient(self.app) as anonymous:
            for headers in ({}, {'Authorization': 'Bearer invalid-token-value-000000000000'}):
                with self.subTest(headers=headers):
                    response = anonymous.get(path, headers=headers)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.headers['www-authenticate'], 'Bearer')
                    self.assertEqual(response.json()['detail'], 'Not authenticated')

        viewer_headers = {'Authorization': f'Bearer {self.viewer_token}'}
        with TestClient(self.app, headers=viewer_headers) as viewer:
            self.assertEqual(viewer.get(path).status_code, 200)
            response = viewer.post('/workflows', json={'name': 'forbidden'})
            self.assertEqual((response.status_code, response.json()['detail']),
                             (403, 'Operator role required'))
            response = viewer.post(path + '/transitions',
                                   json={'status': 'running', 'expected_version': 0})
            self.assertEqual((response.status_code, response.json()['detail']),
                             (403, 'Operator role required'))

        with TestClient(self.app, headers={
            'Authorization': f'Bearer {self.outsider_token}'
        }) as outsider:
            response = outsider.get(path)
            self.assertEqual((response.status_code, response.json()['detail']),
                             (404, 'Workflow not found'))

        with TestClient(self.app, headers={
            'Authorization': f'Bearer {self.other_operator_token}'
        }) as operator:
            self.assertEqual(operator.get(path).status_code, 200)
            response = operator.post(path + '/transitions',
                                     json={'status': 'running', 'expected_version': 0})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['events'][-1]['actor_id'], 'other-operator')

    def test_expired_and_revoked_tokens_share_invalid_token_contract(self):
        now = datetime(2026, 10, 26, 20, tzinfo=timezone.utc)
        principal = Principal('synthetic-operator', Role.OPERATOR)
        authenticator = TokenAuthenticator({
            'expired-test-token-0000000000000000': Credential(
                principal, expires_at=now),
            'revoked-test-token-0000000000000000': Credential(
                principal, revoked=True),
        }, clock=lambda: now)
        with TestClient(create_app(self.repository, authenticator)) as client:
            for token in ('expired-test-token-0000000000000000',
                          'revoked-test-token-0000000000000000'):
                with self.subTest(token=token):
                    response = client.post('/workflows', json={'name': 'denied'}, headers={
                        'Authorization': f'Bearer {token}',
                    })
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.headers['www-authenticate'], 'Bearer')
                    self.assertEqual(response.json()['detail'], 'Not authenticated')

    def test_operator_audit_query_paginates_and_filters(self):
        first_path = self.create().headers['Location']
        self.transition(first_path, 'running')
        second_path = self.create().headers['Location']

        first_page = self.client.get('/audit/events', params={'limit': 2})
        self.assertEqual(first_page.status_code, 200)
        first_body = first_page.json()
        self.assertEqual(len(first_body['items']), 2)
        self.assertIsNotNone(first_body['next_cursor'])
        self.assertGreater(first_body['items'][0]['event_id'],
                           first_body['items'][1]['event_id'])
        self.assertEqual(first_body['items'][0]['workflow_id'], second_path.rsplit('/', 1)[-1])

        second_page = self.client.get('/audit/events', params={
            'limit': 2, 'cursor': first_body['next_cursor'],
        }).json()
        self.assertEqual(len(second_page['items']), 1)
        self.assertIsNone(second_page['next_cursor'])
        seen = {item['event_id'] for item in first_body['items']}
        self.assertTrue(seen.isdisjoint(item['event_id'] for item in second_page['items']))

        filtered = self.client.get('/audit/events', params={
            'subject': 'synthetic-operator', 'status': 'running',
        }).json()
        self.assertEqual(len(filtered['items']), 1)
        self.assertEqual(filtered['items'][0]['status'], 'running')
        self.assertEqual(filtered['items'][0]['actor_id'], 'synthetic-operator')

    def test_audit_query_requires_operator(self):
        self.create()
        with TestClient(self.app, headers={
            'Authorization': f'Bearer {self.viewer_token}'
        }) as viewer:
            response = viewer.get('/audit/events')
            self.assertEqual((response.status_code, response.json()['detail']),
                             (403, 'Operator role required'))

    def test_audit_query_rejects_unbounded_or_invalid_filters(self):
        for params in ({'limit': 0}, {'limit': 101}, {'cursor': 0},
                       {'subject': ' actor '}, {'status': 'unknown'}):
            with self.subTest(params=params):
                self.assertEqual(
                    self.client.get('/audit/events', params=params).status_code, 422
                )

    def test_workflow_list_is_owner_aware_and_operator_can_see_all(self):
        own = self.create().json()
        with TestClient(self.app, headers={
            'Authorization': f'Bearer {self.other_operator_token}'
        }) as other_operator:
            other = other_operator.post('/workflows', json={'name': 'other workflow'}).json()

        with TestClient(self.app, headers={
            'Authorization': f'Bearer {self.viewer_token}'
        }) as viewer:
            response = viewer.get('/workflows')
            self.assertEqual(response.status_code, 200)
            self.assertEqual([item['id'] for item in response.json()['items']], [own['id']])
            self.assertNotIn('events', response.json()['items'][0])

        operator_items = self.client.get('/workflows').json()['items']
        self.assertEqual({item['id'] for item in operator_items}, {own['id'], other['id']})

    def test_workflow_list_paginates_stably_and_filters_status(self):
        oldest_path = self.create().headers['Location']
        self.create()
        self.create()
        first_page = self.client.get('/workflows', params={'limit': 2}).json()
        self.assertEqual(len(first_page['items']), 2)
        self.assertIsNotNone(first_page['next_cursor'])

        self.create()
        second_page = self.client.get('/workflows', params={
            'limit': 2, 'cursor': first_page['next_cursor'],
        }).json()
        self.assertEqual([item['id'] for item in second_page['items']],
                         [oldest_path.rsplit('/', 1)[-1]])
        first_ids = {item['id'] for item in first_page['items']}
        self.assertTrue(first_ids.isdisjoint(item['id'] for item in second_page['items']))

        self.transition(oldest_path, 'running')
        filtered = self.client.get('/workflows', params={'status': 'running'}).json()
        self.assertEqual([item['id'] for item in filtered['items']],
                         [oldest_path.rsplit('/', 1)[-1]])

    def test_workflow_list_rejects_bad_bounds_and_cross_owner_cursor(self):
        own = self.create().json()
        with TestClient(self.app, headers={
            'Authorization': f'Bearer {self.other_operator_token}'
        }) as other_operator:
            hidden = other_operator.post('/workflows', json={'name': 'hidden'}).json()

        with TestClient(self.app, headers={
            'Authorization': f'Bearer {self.viewer_token}'
        }) as viewer:
            response = viewer.get('/workflows', params={'cursor': hidden['id']})
            self.assertEqual((response.status_code, response.json()['detail']),
                             (422, 'Invalid workflow cursor'))
            self.assertEqual(viewer.get('/workflows', params={
                'cursor': own['id']
            }).status_code, 200)

        for params in ({'limit': 0}, {'limit': 101}, {'cursor': 'not-a-uuid'},
                       {'status': 'unknown'}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get('/workflows', params=params).status_code, 422)
