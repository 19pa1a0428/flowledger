import json
import unittest
from datetime import datetime, timedelta, timezone

from flowledger.auth import Credential, Principal, Role, TokenAuthenticator


class AuthenticationTests(unittest.TestCase):
    def test_exact_token_returns_principal(self):
        token = 'synthetic-secret-token-000000000000'
        principal = Principal('synthetic-user', Role.OPERATOR)
        authenticator = TokenAuthenticator({token: principal})
        self.assertEqual(authenticator.authenticate(token), principal)
        self.assertIsNone(authenticator.authenticate(token + 'x'))
        self.assertIsNone(authenticator.authenticate(''))

    def test_json_configuration_and_fail_closed_default(self):
        value = json.dumps([{
            'token': 'synthetic-viewer-token-00000000000',
            'subject': 'synthetic-viewer',
            'role': 'viewer',
        }])
        authenticator = TokenAuthenticator.from_json(value)
        self.assertEqual(authenticator.authenticate('synthetic-viewer-token-00000000000'),
                         Principal('synthetic-viewer', Role.VIEWER))
        self.assertIsNone(TokenAuthenticator.from_json(None).authenticate('anything'))

    def test_configuration_validation(self):
        invalid = [
            'not-json', '{}',
            json.dumps([{'token': 'short', 'subject': 'user', 'role': 'viewer'}]),
            json.dumps([{'token': 'x' * 32, 'subject': ' user ', 'role': 'viewer'}]),
            json.dumps([{'token': 'x' * 32, 'subject': 'user', 'role': 'admin'}]),
            json.dumps([{'token': 'x' * 32, 'subject': 'user', 'role': 'viewer',
                         'extra': True}]),
            json.dumps([{'token': 'x' * 32, 'subject': 'user', 'role': 'viewer',
                         'expires_at': '2026-10-26T20:00:00'}]),
            json.dumps([{'token': 'x' * 32, 'subject': 'user', 'role': 'viewer',
                         'expires_at': 42}]),
            json.dumps([{'token': 'x' * 32, 'subject': 'user', 'role': 'viewer',
                         'revoked': 1}]),
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    TokenAuthenticator.from_json(value)

    def test_expiry_uses_injected_clock_and_expires_at_boundary(self):
        token = 'synthetic-expiring-token-0000000000'
        principal = Principal('synthetic-user', Role.VIEWER)
        now = datetime(2026, 10, 26, 20, tzinfo=timezone.utc)
        current = [now - timedelta(microseconds=1)]
        authenticator = TokenAuthenticator({
            token: Credential(principal, expires_at=now),
        }, clock=lambda: current[0])

        self.assertEqual(authenticator.authenticate(token), principal)
        current[0] = now
        self.assertIsNone(authenticator.authenticate(token))
        current[0] = now + timedelta(days=1)
        self.assertIsNone(authenticator.authenticate(token))

    def test_revoked_credential_is_denied_without_affecting_active_peer(self):
        principal = Principal('synthetic-user', Role.OPERATOR)
        authenticator = TokenAuthenticator({
            'synthetic-revoked-token-0000000000': Credential(principal, revoked=True),
            'synthetic-active-token-000000000000': principal,
        })
        self.assertIsNone(authenticator.authenticate(
            'synthetic-revoked-token-0000000000'))
        self.assertEqual(authenticator.authenticate(
            'synthetic-active-token-000000000000'), principal)

    def test_json_configuration_parses_utc_expiry_and_revocation(self):
        now = datetime(2026, 10, 26, 20, tzinfo=timezone.utc)
        value = json.dumps([
            {'token': 'synthetic-expiring-token-0000000000',
             'subject': 'future-user', 'role': 'viewer',
             'expires_at': '2026-10-26T20:00:01Z'},
            {'token': 'synthetic-revoked-token-0000000000',
             'subject': 'revoked-user', 'role': 'operator', 'revoked': True},
        ])
        authenticator = TokenAuthenticator.from_json(value, clock=lambda: now)
        self.assertEqual(
            authenticator.authenticate('synthetic-expiring-token-0000000000'),
            Principal('future-user', Role.VIEWER),
        )
        self.assertIsNone(authenticator.authenticate(
            'synthetic-revoked-token-0000000000'))


if __name__ == '__main__':
    unittest.main()
