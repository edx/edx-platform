"""
Config for the user_api app.
"""

from django.apps import AppConfig


class UserApiConfig(AppConfig):
    """
    Config for the user_api app.
    """
    name = 'openedx.core.djangoapps.user_api'
    label = 'user_api'

    def ready(self):
        """
        Connect signal handlers.
        """
        from . import handlers  # pylint: disable=unused-import
