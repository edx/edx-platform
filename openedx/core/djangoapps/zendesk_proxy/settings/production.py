""" AWS settings for zendesk proxy."""


def plugin_settings(settings):
    """Override Zendesk OAuth settings from ENV_TOKENS and AUTH_TOKENS."""
    settings.ZENDESK_URL = settings.ENV_TOKENS.get('ZENDESK_URL', settings.ZENDESK_URL)
    settings.ZENDESK_OAUTH_CLIENT_ID = settings.AUTH_TOKENS.get('ZENDESK_OAUTH_CLIENT_ID')
    settings.ZENDESK_OAUTH_CLIENT_SECRET = settings.AUTH_TOKENS.get('ZENDESK_OAUTH_CLIENT_SECRET')
    settings.ZENDESK_OAUTH_SCOPE = settings.ENV_TOKENS.get('ZENDESK_OAUTH_SCOPE', settings.ZENDESK_OAUTH_SCOPE)
    settings.ZENDESK_OAUTH_TOKEN_EXPIRES_IN = settings.ENV_TOKENS.get(
        'ZENDESK_OAUTH_TOKEN_EXPIRES_IN', settings.ZENDESK_OAUTH_TOKEN_EXPIRES_IN
    )
