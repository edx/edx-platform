""" Common settings for zendesk proxy."""


def plugin_settings(settings):
    """Add default Zendesk OAuth client-credentials settings."""
    settings.ZENDESK_URL = None
    settings.ZENDESK_OAUTH_CLIENT_ID = None
    settings.ZENDESK_OAUTH_CLIENT_SECRET = None
    settings.ZENDESK_OAUTH_SCOPE = 'tickets:write'
    settings.ZENDESK_OAUTH_TOKEN_EXPIRES_IN = 86400
