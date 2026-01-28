"""
Utility functions for edx-ace.
"""
import logging
from openedx.features.course_experience import (
    ENABLE_SES_FOR_COURSEUPDATE,
)

log = logging.getLogger(__name__)


SES_MESSAGE_FLAG_MAP = {
    'course_update': ENABLE_SES_FOR_COURSEUPDATE,
}


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


def should_route_to_ses(msg):
    """
    Determine whether an ACE message should be routed via SES.

    Routing is controlled by message-specific waffle flags.
    """
    flag = SES_MESSAGE_FLAG_MAP.get(msg.name)

    if not flag:
        return False

    # Environment-level flag (not course-scoped)
    return flag.is_enabled()
