"""
Tests of Zendesk interaction utility functions
"""


import json
from collections import OrderedDict

from unittest.mock import MagicMock, patch
from django.core.cache import cache
from django.test.utils import override_settings

import ddt
from openedx.core.djangoapps.zendesk_proxy.utils import (
    ZENDESK_OAUTH_ACCESS_TOKEN_CACHE_KEY,
    create_zendesk_ticket,
    post_additional_info_as_comment,
)
from openedx.core.lib.api.test_utils import ApiTestCase

ZENDESK_URL = "https://www.superrealurlsthataredefinitelynotfake.com"
TOKEN_URL = ZENDESK_URL + "/oauth/tokens"
TICKET_URL = ZENDESK_URL + "/api/v2/tickets.json"

LOCMEM_CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'zendesk_proxy_test',
    }
}


def _mock_response(status_code, json_data=None, raise_on_json=False):
    """Build a MagicMock standing in for a `requests` response."""
    response = MagicMock(status_code=status_code)
    if raise_on_json:
        response.json.side_effect = ValueError("not json")
    else:
        response.json.return_value = json_data if json_data is not None else {}
    response.content = json.dumps(json_data).encode('utf-8') if json_data is not None else b''
    return response


def _mock_token_response(access_token='test_access_token', expires_in=3600, status_code=200):
    return _mock_response(status_code, {
        'access_token': access_token,
        'token_type': 'bearer',
        'expires_in': expires_in,
        'scope': 'tickets:write',
    })


def _post_side_effect(token_response, ticket_response):
    """
    Return a side_effect function for `requests.post` that routes to the
    token response or ticket response based on the requested URL.
    """
    def _side_effect(url, data=None, headers=None):  # pylint: disable=unused-argument
        if url == TOKEN_URL:
            return token_response
        return ticket_response
    return _side_effect


@ddt.ddt
@override_settings(
    ZENDESK_URL=ZENDESK_URL,
    ZENDESK_OAUTH_CLIENT_ID="test_client_id",
    ZENDESK_OAUTH_CLIENT_SECRET="test_client_secret",
    ZENDESK_OAUTH_SCOPE="tickets:write",
    ZENDESK_OAUTH_TOKEN_EXPIRES_IN=3600,
    ZENDESK_GROUP_ID_MAPPING={"Financial Assistance": 123},
)
class TestUtils(ApiTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    def setUp(self):
        self.request_data = {
            'email': 'JohnQStudent@example.com',
            'name': 'John Q. Student',
            'subject': 'Python Unit Test Help Request',
            'body': "Help! I'm trapped in a unit test factory and I can't get out!",
        }
        cache.clear()
        return super().setUp()

    def _create_ticket(self, **kwargs):
        """Create a ticket from the test request data, with optional overrides."""
        params = dict(
            requester_name=self.request_data['name'],
            requester_email=self.request_data['email'],
            subject=self.request_data['subject'],
            body=self.request_data['body'],
        )
        params.update(kwargs)
        return create_zendesk_ticket(**params)

    # -- Configuration --

    @ddt.data(
        {'ZENDESK_URL': None},
        {'ZENDESK_OAUTH_CLIENT_ID': None},
        {'ZENDESK_OAUTH_CLIENT_SECRET': None},
    )
    def test_missing_settings(self, overridden_settings):
        with override_settings(**overridden_settings):
            status_code = self._create_ticket()
        assert status_code == 503

    # -- Basic ticket creation status codes --

    @ddt.data(201, 400, 403, 404, 500)
    def test_zendesk_status_codes(self, mock_code):
        token_response = _mock_token_response()
        ticket_response = _mock_response(mock_code)
        with patch('requests.post', side_effect=_post_side_effect(token_response, ticket_response)):
            status_code = self._create_ticket()
        assert status_code == mock_code

    def test_401_without_invalid_token_error_does_not_retry(self):
        """A 401 that isn't 'invalid_token' should not trigger token regeneration."""
        token_response = _mock_token_response()
        ticket_response = _mock_response(401, {'error': 'not_authorized'})
        with patch(
            'requests.post', side_effect=_post_side_effect(token_response, ticket_response)
        ) as mock_post:
            status_code = self._create_ticket()
        assert status_code == 401
        assert mock_post.call_count == 2  # one token request, one ticket request

    def test_401_with_non_json_body_does_not_retry(self):
        """A 401 with a non-JSON body can't be positively identified as invalid_token, so no retry."""
        token_response = _mock_token_response()
        ticket_response = _mock_response(401, raise_on_json=True)
        with patch(
            'requests.post', side_effect=_post_side_effect(token_response, ticket_response)
        ) as mock_post:
            status_code = self._create_ticket()
        assert status_code == 401
        assert mock_post.call_count == 2  # one token request, one ticket request

    def test_401_with_non_dict_json_body_does_not_retry(self):
        """A 401 with a non-dict JSON body (e.g. a list) must not raise and must not retry."""
        token_response = _mock_token_response()
        ticket_response = _mock_response(401)
        ticket_response.json.return_value = ['unexpected', 'array']
        with patch(
            'requests.post', side_effect=_post_side_effect(token_response, ticket_response)
        ) as mock_post:
            status_code = self._create_ticket()
        assert status_code == 401
        assert mock_post.call_count == 2  # one token request, one ticket request

    def test_unexpected_error_pinging_zendesk(self):
        with patch('requests.post', side_effect=Exception("WHAMMY")):
            status_code = self._create_ticket()
        assert status_code == 500

    # -- OAuth token generation --

    def test_token_request_payload(self):
        token_response = _mock_token_response(access_token='shiny-token')
        ticket_response = _mock_response(201)
        with patch(
            'requests.post', side_effect=_post_side_effect(token_response, ticket_response)
        ) as mock_post:
            status_code = self._create_ticket()

        assert status_code == 201
        token_call = next(call for call in mock_post.call_args_list if call.args[0] == TOKEN_URL)
        sent_payload = json.loads(token_call.kwargs['data'])
        assert sent_payload == {
            'grant_type': 'client_credentials',
            'client_id': 'test_client_id',
            'client_secret': 'test_client_secret',
            'scope': 'tickets:write',
            'expires_in': 3600,
        }

        ticket_call = next(call for call in mock_post.call_args_list if call.args[0] == TICKET_URL)
        assert ticket_call.kwargs['headers']['Authorization'] == 'Bearer shiny-token'

    # -- Token caching --

    @override_settings(CACHES=LOCMEM_CACHES)
    def test_token_is_cached_and_reused(self):
        token_response = _mock_token_response()
        ticket_response = _mock_response(201)
        with patch(
            'requests.post', side_effect=_post_side_effect(token_response, ticket_response)
        ) as mock_post:
            self._create_ticket()
            self._create_ticket()

        token_calls = [call for call in mock_post.call_args_list if call.args[0] == TOKEN_URL]
        ticket_calls = [call for call in mock_post.call_args_list if call.args[0] == TICKET_URL]
        assert len(token_calls) == 1
        assert len(ticket_calls) == 2

    def test_token_cache_ttl_uses_expiry_buffer(self):
        token_response = _mock_token_response(expires_in=120)
        ticket_response = _mock_response(201)
        with patch('requests.post', side_effect=_post_side_effect(token_response, ticket_response)):
            with patch('openedx.core.djangoapps.zendesk_proxy.utils.cache') as mock_cache:
                mock_cache.get.return_value = None
                self._create_ticket()

        mock_cache.set.assert_called_once_with(
            ZENDESK_OAUTH_ACCESS_TOKEN_CACHE_KEY, 'test_access_token', 60
        )

    # -- Missing/malformed token response --

    def test_token_response_missing_access_token(self):
        token_response = _mock_token_response()
        token_response.json.return_value = {'token_type': 'bearer'}
        ticket_response = _mock_response(201)
        with patch(
            'requests.post', side_effect=_post_side_effect(token_response, ticket_response)
        ) as mock_post:
            status_code = self._create_ticket()

        assert status_code == 503
        ticket_calls = [call for call in mock_post.call_args_list if call.args[0] == TICKET_URL]
        assert not ticket_calls

    def test_token_response_malformed_non_json(self):
        token_response = _mock_response(200, raise_on_json=True)
        ticket_response = _mock_response(201)
        with patch(
            'requests.post', side_effect=_post_side_effect(token_response, ticket_response)
        ) as mock_post:
            status_code = self._create_ticket()

        assert status_code == 503
        ticket_calls = [call for call in mock_post.call_args_list if call.args[0] == TICKET_URL]
        assert not ticket_calls

    @ddt.data(
        {'access_token': 'test_access_token', 'expires_in': 3600, 'scope': 'tickets:write'},
        {'access_token': 'test_access_token', 'token_type': 'bearer', 'scope': 'tickets:write'},
        {'access_token': 'test_access_token', 'token_type': 'bearer', 'expires_in': 3600},
        {'access_token': 'test_access_token', 'token_type': 'mac', 'expires_in': 3600, 'scope': 'tickets:write'},
        {'access_token': 'test_access_token', 'token_type': 'bearer', 'expires_in': 0, 'scope': 'tickets:write'},
    )
    def test_token_response_invalid_expected_fields(self, token_data):
        token_response = _mock_response(200, token_data)
        ticket_response = _mock_response(201)
        with patch(
            'requests.post', side_effect=_post_side_effect(token_response, ticket_response)
        ) as mock_post:
            status_code = self._create_ticket()

        assert status_code == 503
        ticket_calls = [call for call in mock_post.call_args_list if call.args[0] == TICKET_URL]
        assert not ticket_calls

    # -- Financial Assistance / additional info flow --

    def test_financial_assistant_ticket(self):
        """ Test Financial Assistance request ticket. """
        ticket_creation_response_data = {
            "ticket": {
                "id": 35436,
                "subject": "My printer is on fire!",
            }
        }
        token_response = _mock_token_response(access_token='fa-token')
        ticket_response = _mock_response(201, ticket_creation_response_data)
        with patch('requests.post', side_effect=_post_side_effect(token_response, ticket_response)):
            with patch('requests.put', return_value=MagicMock(status_code=200)) as mock_put:
                status_code = self._create_ticket(
                    group='Financial Assistance',
                    additional_info=OrderedDict(
                        (
                            ('Username', 'test'),
                            ('Full Name', 'Legal Name'),
                            ('Course ID', 'course_key'),
                            ('Country', 'Country'),
                        )
                    ),
                )
        assert status_code == 200
        (put_args, put_kwargs) = mock_put.call_args
        assert put_args == (f'{ZENDESK_URL}/api/v2/tickets/35436.json',)
        assert put_kwargs['headers']['Authorization'] == 'Bearer fa-token'

    def test_additional_info_not_posted_when_ticket_creation_fails(self):
        token_response = _mock_token_response()
        ticket_response = _mock_response(400)
        with patch('requests.post', side_effect=_post_side_effect(token_response, ticket_response)):
            with patch('requests.put') as mock_put:
                status_code = self._create_ticket(additional_info={'Username': 'test'})

        assert status_code == 400
        mock_put.assert_not_called()

    # -- 401 invalid_token retry behavior --

    def test_401_invalid_token_triggers_single_retry_and_succeeds(self):
        first_token_response = _mock_token_response(access_token='expired-token')
        second_token_response = _mock_token_response(access_token='fresh-token')
        unauthorized_response = _mock_response(401, {'error': 'invalid_token'})
        success_response = _mock_response(201)

        token_responses = iter([first_token_response, second_token_response])
        ticket_responses = iter([unauthorized_response, success_response])

        def _side_effect(url, data=None, headers=None):  # pylint: disable=unused-argument
            if url == TOKEN_URL:
                return next(token_responses)
            return next(ticket_responses)

        with patch('requests.post', side_effect=_side_effect) as mock_post:
            status_code = self._create_ticket()

        assert status_code == 201
        token_calls = [call for call in mock_post.call_args_list if call.args[0] == TOKEN_URL]
        ticket_calls = [call for call in mock_post.call_args_list if call.args[0] == TICKET_URL]
        assert len(token_calls) == 2
        assert len(ticket_calls) == 2
        assert ticket_calls[0].kwargs['headers']['Authorization'] == 'Bearer expired-token'
        assert ticket_calls[1].kwargs['headers']['Authorization'] == 'Bearer fresh-token'

    def test_401_invalid_token_retry_failure_does_not_retry_again(self):
        token_response = _mock_token_response()
        unauthorized_response = _mock_response(401, {'error': 'invalid_token'})

        with patch(
            'requests.post', side_effect=_post_side_effect(token_response, unauthorized_response)
        ) as mock_post:
            status_code = self._create_ticket()

        assert status_code == 401
        token_calls = [call for call in mock_post.call_args_list if call.args[0] == TOKEN_URL]
        ticket_calls = [call for call in mock_post.call_args_list if call.args[0] == TICKET_URL]
        assert len(token_calls) == 2
        assert len(ticket_calls) == 2

    @ddt.data(403, 404, 422, 500)
    def test_non_auth_errors_do_not_regenerate_token(self, error_code):
        token_response = _mock_token_response()
        ticket_response = _mock_response(error_code)
        with patch(
            'requests.post', side_effect=_post_side_effect(token_response, ticket_response)
        ) as mock_post:
            status_code = self._create_ticket()

        assert status_code == error_code
        token_calls = [call for call in mock_post.call_args_list if call.args[0] == TOKEN_URL]
        ticket_calls = [call for call in mock_post.call_args_list if call.args[0] == TICKET_URL]
        assert len(token_calls) == 1
        assert len(ticket_calls) == 1

    # -- Token endpoint failures --

    @ddt.data(400, 401, 500)
    def test_token_endpoint_error_status_fails_cleanly(self, token_status_code):
        token_response = _mock_response(token_status_code, {
            'access_token': 'should-not-be-used',
            'token_type': 'bearer',
            'expires_in': 3600,
            'scope': 'tickets:write',
        })
        ticket_response = _mock_response(201)
        with patch(
            'requests.post', side_effect=_post_side_effect(token_response, ticket_response)
        ) as mock_post:
            status_code = self._create_ticket()

        assert status_code == 503
        ticket_calls = [call for call in mock_post.call_args_list if call.args[0] == TICKET_URL]
        assert not ticket_calls

    def test_token_endpoint_network_failure_fails_cleanly(self):
        import requests as requests_module

        def _side_effect(url, data=None, headers=None):  # pylint: disable=unused-argument
            if url == TOKEN_URL:
                raise requests_module.exceptions.Timeout("timed out")
            raise AssertionError("ticket endpoint should not be called")

        with patch('requests.post', side_effect=_side_effect):
            status_code = self._create_ticket()

        assert status_code == 503

    # -- Security: secrets must never be logged --

    def test_client_secret_not_logged_on_token_failure(self):
        token_response = _mock_response(400, {'error': 'invalid_client'})
        ticket_response = _mock_response(201)
        with patch('requests.post', side_effect=_post_side_effect(token_response, ticket_response)):
            with self.assertLogs('openedx.core.djangoapps.zendesk_proxy.utils', level='ERROR') as log_context:
                self._create_ticket()

        logged_output = '\n'.join(log_context.output)
        assert 'test_client_secret' not in logged_output


@override_settings(
    ZENDESK_URL=ZENDESK_URL,
    ZENDESK_OAUTH_CLIENT_ID="test_client_id",
    ZENDESK_OAUTH_CLIENT_SECRET="test_client_secret",
    ZENDESK_OAUTH_SCOPE="tickets:write",
    ZENDESK_OAUTH_TOKEN_EXPIRES_IN=3600,
)
class TestPostAdditionalInfoAsComment(ApiTestCase):
    """Tests for `post_additional_info_as_comment` in isolation."""

    def setUp(self):
        cache.clear()
        return super().setUp()

    def test_uses_oauth_token(self):
        token_response = _mock_token_response(access_token='comment-token')
        with patch('requests.post', return_value=token_response):
            with patch('requests.put', return_value=MagicMock(status_code=200)) as mock_put:
                status_code = post_additional_info_as_comment(35436, {'Username': 'test'})

        assert status_code == 200
        assert mock_put.call_args.kwargs['headers']['Authorization'] == 'Bearer comment-token'
