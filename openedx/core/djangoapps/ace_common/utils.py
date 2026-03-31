"""
Utility functions for edx-ace.
"""
import logging
from django.conf import settings
from edx_toggles.toggles import WaffleFlag
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers


log = logging.getLogger(__name__)

# .. toggle_name: user_authn.enable_ses_for_account_activation
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Route account activation emails via SES using ACE.
# .. toggle_use_cases: opt_in, temporary
# .. toggle_creation_date: 2026-03-31
# .. toggle_target_removal_date: None
# .. toggle_warning: Controls SES routing for account activation emails.

ENABLE_SES_FOR_ACCOUNT_ACTIVATION = WaffleFlag(
    'user_authn.enable_ses_for_account_activation',
    __name__,
)


def apply_ses_routing_if_enabled(msg):
    """
    Apply SES routing to ACE message if flag is enabled.
    """
    if not ENABLE_SES_FOR_ACCOUNT_ACTIVATION.is_enabled():
        return msg

    if msg.options is None:
        msg.options = {}

    msg.options.update({
        'transactional': True,
        'override_default_channel': 'django_email',
        'from_address': configuration_helpers.get_value(
            'ACTIVATION_EMAIL_FROM_ADDRESS'
        ) or configuration_helpers.get_value(
            'email_from_address',
            settings.DEFAULT_FROM_EMAIL
        ),
    })

    return msg


def setup_firebase_app(firebase_credentials, app_name='fcm-app'):
    """
    Returns a Firebase app instance if the Firebase credentials are provided.
    """
    import firebase_admin  # pylint: disable=import-outside-toplevel

    if firebase_credentials:
        try:
            app = firebase_admin.get_app(app_name)
        except ValueError:
            certificate = firebase_admin.credentials.Certificate(firebase_credentials)
            app = firebase_admin.initialize_app(certificate, name=app_name)
        return app
