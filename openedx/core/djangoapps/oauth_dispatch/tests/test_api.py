""" Tests for OAuth Dispatch python API module. """


import unittest

from django.conf import settings
from django.http import HttpRequest
from django.test import TestCase
from oauth2_provider.models import AccessToken

from common.djangoapps.student.tests.factories import UserFactory

OAUTH_PROVIDER_ENABLED = settings.FEATURES.get('ENABLE_OAUTH2_PROVIDER')
if OAUTH_PROVIDER_ENABLED:
    from openedx.core.djangoapps.oauth_dispatch import api
    from openedx.core.djangoapps.oauth_dispatch.adapters import DOTAdapter
    from openedx.core.djangoapps.oauth_dispatch.tests.constants import DUMMY_REDIRECT_URL

EXPECTED_DEFAULT_EXPIRES_IN = 36000


@unittest.skipUnless(OAUTH_PROVIDER_ENABLED, 'OAuth2 not enabled')
class TestOAuthDispatchAPI(TestCase):
    """ Tests for oauth_dispatch's api.py module. """
    def setUp(self):
        super(TestOAuthDispatchAPI, self).setUp()
        self.adapter = DOTAdapter()
        self.user = UserFactory()
        self.client = self.adapter.create_public_client(
            name='public app',
            user=self.user,
            redirect_uri=DUMMY_REDIRECT_URL,
            client_id='public-client-id',
        )

    def _assert_stored_token(self, stored_token_value, expected_token_user, expected_client):
        stored_access_token = AccessToken.objects.get(token=stored_token_value)
        self.assertEqual(stored_access_token.user.id, expected_token_user.id)
        self.assertEqual(stored_access_token.application.client_id, expected_client.client_id)
        self.assertEqual(stored_access_token.application.user.id, expected_client.user.id)

    def test_create_token_success(self):
        token = api.create_dot_access_token(HttpRequest(), self.user, self.client)
        self.assertTrue(token['access_token'])
        self.assertTrue(token['refresh_token'])
        self.assertDictContainsSubset(
            {
                u'token_type': u'Bearer',
                u'expires_in': EXPECTED_DEFAULT_EXPIRES_IN,
                u'scope': u'',
            },
            token,
        )
        self._assert_stored_token(token['access_token'], self.user, self.client)

    def test_create_token_another_user(self):
        another_user = UserFactory()
        token = api.create_dot_access_token(HttpRequest(), another_user, self.client)
        self._assert_stored_token(token['access_token'], another_user, self.client)

    def test_create_token_overrides(self):
        expires_in = 4800
        token = api.create_dot_access_token(
            HttpRequest(), self.user, self.client, expires_in=expires_in, scopes=['profile'],
        )
        self.assertDictContainsSubset({u'scope': u'profile'}, token)
        self.assertDictContainsSubset({u'expires_in': expires_in}, token)
