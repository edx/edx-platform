"""Tests for zendesk_proxy views."""


from copy import deepcopy
import json
from unittest.mock import MagicMock, patch

import ddt
from django.urls import reverse
from django.test.utils import override_settings

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.zendesk_proxy.v1.views import ZendeskProxyThrottle
from openedx.core.lib.api.test_utils import ApiTestCase

ZENDESK_URL = "https://www.superrealurlsthataredefinitelynotfake.com"
TOKEN_URL = ZENDESK_URL + "/oauth/tokens"
TICKET_URL = ZENDESK_URL + "/api/v2/tickets.json"
ACCESS_TOKEN = "abcdefghijklmnopqrstuvwxyz1234567890"


def _post_side_effect(ticket_response):
    """Route requests.post to a fake OAuth token response, or the given ticket response."""
    def _side_effect(url, data=None, headers=None):  # pylint: disable=unused-argument
        if url == TOKEN_URL:
            token_response = MagicMock(status_code=200)
            token_response.json.return_value = {
                'access_token': ACCESS_TOKEN, 'token_type': 'bearer',
                'expires_in': 3600, 'scope': 'tickets:write',
            }
            return token_response
        return ticket_response
    return _side_effect


@ddt.ddt
@override_settings(
    ZENDESK_URL=ZENDESK_URL,
    ZENDESK_OAUTH_CLIENT_ID="test_client_id",
    ZENDESK_OAUTH_CLIENT_SECRET="test_client_secret",
)
class ZendeskProxyTestCase(ApiTestCase):
    """Tests for zendesk_proxy views."""

    def setUp(self):
        self.url = reverse('zendesk_proxy_v1')
        self.user = UserFactory(username='test', password='test123')
        self.client.login(username='test', password='test123')
        self.request_data = {
            'requester': {
                'email': self.user.email,
                'name': self.user.username
            },
            'subject': 'Python Unit Test Help Request',
            'comment': {
                'body': "Help! I'm trapped in a unit test factory and I can't get out!",
            },
            'tags': ['python_unit_test'],
            'custom_fields': [
                {
                    'id': '001',
                    'value': 'demo-course'
                },
                {
                    'id': '002',
                    'value': 'https://www.example.com'
                }
            ],
        }
        return super().setUp()

    @ddt.data(
        True, False
    )
    def test_post(self, user_activation_status):
        """
        Test both active and inactive users can request Zendesk Proxy for the
        submission of support tickets.
        """
        self.user.is_active = user_activation_status
        self.user.save()
        with patch('requests.post', side_effect=_post_side_effect(MagicMock(status_code=201))) as mock_post:
            response = self.request_without_auth(
                'post',
                self.url,
                data=json.dumps(self.request_data),
                content_type='application/json'
            )
            self.assertHttpCreated(response)
            (mock_args, mock_kwargs) = mock_post.call_args
            assert mock_args == (TICKET_URL,)
            self.assertCountEqual(mock_kwargs.keys(), ['headers', 'data'])
            assert mock_kwargs['headers'] == {
                'content-type': 'application/json', 'Authorization': f'Bearer {ACCESS_TOKEN}'
            }
            assert json.loads(mock_kwargs['data']) == {
                'ticket': {
                    'comment': {
                        'body': "Help! I'm trapped in a unit test factory and I can't get out!", 'uploads': None
                    },
                    'custom_fields': [
                        {'id': '001', 'value': 'demo-course'},
                        {'id': '002', 'value': 'https://www.example.com'},
                    ],
                    'requester': {'email': self.user.email, 'name': self.user.username},
                    'subject': 'Python Unit Test Help Request', 'tags': ['python_unit_test']
                }
            }

    @ddt.data('subject', 'tags')
    def test_bad_request(self, key_to_delete):
        test_data = deepcopy(self.request_data)
        _ = test_data.pop(key_to_delete)

        response = self.request_without_auth(
            'post',
            self.url,
            data=json.dumps(test_data),
            content_type='application/json'
        )
        self.assertHttpBadRequest(response)

    @override_settings(
        CACHES={
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'LOCATION': 'zendesk_proxy',
            }
        }
    )
    def test_rate_limiting(self):
        """
        Confirm rate limits work as expected. Note that drf's rate limiting makes use of the default cache to enforce
        limits; that's why this test needs a "real" default cache (as opposed to the usual-for-tests DummyCache)
        """

        for _ in range(ZendeskProxyThrottle().num_requests):
            self.request_without_auth('post', self.url)
        response = self.request_without_auth('post', self.url)
        assert response.status_code == 429
