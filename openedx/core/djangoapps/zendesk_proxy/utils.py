"""
Utility functions for zendesk interaction.
"""


import json
import logging
from urllib.parse import urljoin  # pylint: disable=import-error

import requests
from django.conf import settings
from django.core.cache import cache
from rest_framework import status

log = logging.getLogger(__name__)

# Cache key for the Zendesk OAuth access token obtained via the Client
# Credentials grant. Uses Django's shared cache so the token is reusable
# across all LMS/CMS worker processes, instead of process-local state.
ZENDESK_OAUTH_ACCESS_TOKEN_CACHE_KEY = 'zendesk_proxy.oauth_access_token'

# Safety buffer (in seconds) subtracted from the token's expires_in, so a
# token is never used right at its expiration boundary.
ZENDESK_OAUTH_TOKEN_EXPIRY_BUFFER = 60


def _std_error_message(details, payload):
    """Internal helper to standardize error message. This allows for simpler splunk alerts."""
    return f'zendesk_proxy action required\n{details}\nNo ticket created for payload {payload}'


def _zendesk_configured():
    """Return True if the settings required to authenticate with Zendesk are present."""
    return bool(settings.ZENDESK_URL and settings.ZENDESK_OAUTH_CLIENT_ID and settings.ZENDESK_OAUTH_CLIENT_SECRET)


def _request_zendesk_access_token():
    """
    Request a new OAuth access token from Zendesk using the Client Credentials
    grant. This grant is intended for confidential/server-side clients: it
    does not require interactive user authorization and does not return a
    refresh token.

    Note: the resulting access token acts on behalf of the Zendesk
    administrator who owns the OAuth client, so that admin must retain
    sufficient Zendesk access for the token to keep working. This is an
    operational/deployment concern, not something this code can enforce.

    Returns a (access_token, expires_in) tuple, or (None, None) on failure.
    """
    url = urljoin(settings.ZENDESK_URL, '/oauth/tokens')
    payload = json.dumps({
        'grant_type': 'client_credentials',
        'client_id': settings.ZENDESK_OAUTH_CLIENT_ID,
        'client_secret': settings.ZENDESK_OAUTH_CLIENT_SECRET,
        'scope': settings.ZENDESK_OAUTH_SCOPE,
        'expires_in': settings.ZENDESK_OAUTH_TOKEN_EXPIRES_IN,
    })

    try:
        response = requests.post(url, data=payload, headers={'content-type': 'application/json'})
    except requests.RequestException:
        log.exception('Zendesk OAuth token request failed')
        return None, None

    if not status.HTTP_200_OK <= response.status_code < status.HTTP_300_MULTIPLE_CHOICES:
        log.error(f'Zendesk OAuth token request failed: HTTP {response.status_code}')
        return None, None

    try:
        token_data = response.json()
    except ValueError:
        log.error(f'Zendesk OAuth token request returned a malformed response: HTTP {response.status_code}')
        return None, None

    if not isinstance(token_data, dict):
        log.error(f'Zendesk OAuth token request returned a malformed response: HTTP {response.status_code}')
        return None, None

    access_token = token_data.get('access_token')
    if not access_token:
        log.error(f'Zendesk OAuth token request did not return an access token: HTTP {response.status_code}')
        return None, None

    token_type = token_data.get('token_type')
    if not isinstance(token_type, str) or token_type.lower() != 'bearer':
        log.error(f'Zendesk OAuth token request returned an invalid token type: HTTP {response.status_code}')
        return None, None

    if not token_data.get('scope'):
        log.error(f'Zendesk OAuth token request returned an invalid scope: HTTP {response.status_code}')
        return None, None

    expires_in = token_data.get('expires_in')
    if not isinstance(expires_in, int) or expires_in <= 0:
        log.error(f'Zendesk OAuth token request returned an invalid expires_in: HTTP {response.status_code}')
        return None, None

    return access_token, expires_in


def _get_zendesk_access_token(force_refresh=False):
    """
    Return a Zendesk OAuth access token, fetching and caching a new one if
    necessary, so a new token is not requested for every Zendesk API call.
    """
    if not force_refresh:
        cached_token = cache.get(ZENDESK_OAUTH_ACCESS_TOKEN_CACHE_KEY)
        if cached_token:
            return cached_token

    access_token, expires_in = _request_zendesk_access_token()
    if not access_token:
        return None

    cache_timeout = max(expires_in - ZENDESK_OAUTH_TOKEN_EXPIRY_BUFFER, 1)
    cache.set(ZENDESK_OAUTH_ACCESS_TOKEN_CACHE_KEY, access_token, cache_timeout)
    return access_token


def _invalidate_zendesk_access_token():
    """Remove any cached Zendesk OAuth access token."""
    cache.delete(ZENDESK_OAUTH_ACCESS_TOKEN_CACHE_KEY)


def _get_request_headers(access_token):
    return {
        'content-type': 'application/json',
        'Authorization': f"Bearer {access_token}",
    }


def _is_invalid_token_response(response):
    """Return True if the response indicates the OAuth access token is invalid/expired."""
    if response.status_code != status.HTTP_401_UNAUTHORIZED:
        return False
    try:
        return response.json().get('error') == 'invalid_token'
    except ValueError:
        return True


def _zendesk_request(method, url, payload):
    """
    Make an authenticated Zendesk API request. Obtains a (cached) OAuth access
    token and retries at most once if Zendesk reports the token as
    invalid/expired.

    Returns the `requests` response, or None if no access token could be
    obtained at all.
    """
    access_token = _get_zendesk_access_token()
    if not access_token:
        return None

    response = method(url, data=payload, headers=_get_request_headers(access_token))

    if _is_invalid_token_response(response):
        log.info('Zendesk access token invalid; requesting replacement')
        _invalidate_zendesk_access_token()
        access_token = _get_zendesk_access_token(force_refresh=True)
        if access_token:
            response = method(url, data=payload, headers=_get_request_headers(access_token))

    return response


def create_zendesk_ticket(
        requester_name,
        requester_email,
        subject,
        body,
        group=None,
        custom_fields=None,
        uploads=None,
        tags=None,
        additional_info=None
):
    """
    Create a Zendesk ticket via API.
    """
    if tags:
        # Remove duplicates from tags list.
        # Pls note: only use tags for lists and sets, as the below will remove the value of a key/value dictionary.
        tags = list(set(tags))

    data = {
        'ticket': {
            'requester': {
                'name': requester_name,
                'email': requester_email
            },
            'subject': subject,
            'comment': {
                'body': body,
                'uploads': uploads
            },
            'custom_fields': custom_fields,
            'tags': tags
        }
    }

    if not _zendesk_configured():
        log.error(_std_error_message("zendesk not configured", data))
        return status.HTTP_503_SERVICE_UNAVAILABLE

    if group:
        if group in settings.ZENDESK_GROUP_ID_MAPPING:
            group_id = settings.ZENDESK_GROUP_ID_MAPPING[group]
            data['ticket']['group_id'] = group_id
        else:
            msg = f"Group ID not found for group {group}. Please update ZENDESK_GROUP_ID_MAPPING"
            log.error(_std_error_message(msg, data))
            return status.HTTP_400_BAD_REQUEST

    # Encode the data to create a JSON payload
    payload = json.dumps(data)

    # Set the request parameters
    url = urljoin(settings.ZENDESK_URL, '/api/v2/tickets.json')

    try:
        response = _zendesk_request(requests.post, url, payload)
        if response is None:
            log.error(_std_error_message('Unable to obtain a Zendesk OAuth access token', payload))
            return status.HTTP_503_SERVICE_UNAVAILABLE

        # Check for HTTP codes other than 201 (Created)
        if response.status_code != status.HTTP_201_CREATED:
            log.error(
                _std_error_message(
                    f'Unexpected response: {response.status_code} - {response.content}',
                    payload
                )
            )
            return response.status_code

        log.debug(f'Successfully created ticket for {requester_email}')

        if additional_info:
            try:
                ticket = response.json()['ticket']
            except (ValueError, KeyError):
                log.error(
                    _std_error_message(
                        "Got an unexpected response from zendesk api. Can't"
                        " get the ticket number to add extra info. {}".format(additional_info),
                        response.content
                    )
                )
                return status.HTTP_400_BAD_REQUEST
            return post_additional_info_as_comment(ticket['id'], additional_info)

        return response.status_code
    except Exception:  # pylint: disable=broad-except
        log.exception(_std_error_message('Internal server error', payload))
        return status.HTTP_500_INTERNAL_SERVER_ERROR


def post_additional_info_as_comment(ticket_id, additional_info):
    """
    Post the Additional Provided as a comment, So that it is only visible
    to management and not students.
    """
    additional_info_string = (
        "Additional information:\n\n" +
        "\n".join(f"{key}: {value}" for (key, value) in additional_info.items() if value is not None)
    )

    data = {
        'ticket': {
            'comment': {
                'body': additional_info_string,
                'public': False
            }
        }
    }

    url = urljoin(settings.ZENDESK_URL, f'api/v2/tickets/{ticket_id}.json')
    payload = json.dumps(data)

    try:
        response = _zendesk_request(requests.put, url, payload)
        if response is None:
            log.error(_std_error_message('Unable to obtain a Zendesk OAuth access token', data))
            return status.HTTP_503_SERVICE_UNAVAILABLE

        if response.status_code == 200:
            log.debug(f'Successfully created comment for ticket {ticket_id}')
        else:
            log.error(
                _std_error_message(
                    f'Unexpected response: {response.status_code} - {response.content}',
                    data
                )
            )
        return response.status_code
    except Exception:  # pylint: disable=broad-except
        log.exception(_std_error_message('Internal server error', data))
        return status.HTTP_500_INTERNAL_SERVER_ERROR
