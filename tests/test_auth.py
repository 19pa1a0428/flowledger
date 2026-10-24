import json
import unittest

from flowledger.auth import Principal, Role, TokenAuthenticator


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
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    TokenAuthenticator.from_json(value)


if __name__ == '__main__':
    unittest.main()
