"""
Signal handlers for the user_api app.
"""
import logging

from django.db import transaction
from django.dispatch import receiver
from openedx_events.learning.signals import COURSE_ENROLLMENT_CREATED

from openedx.core.djangoapps.user_api.models import UserOrgTag
from openedx.core.djangoapps.user_api.toggles import ENABLE_DEFAULT_EMAIL_OPTIN_ON_ENROLLMENT

log = logging.getLogger(__name__)

EMAIL_OPTIN_KEY = 'email-optin'
DEFAULT_EMAIL_OPTIN_VALUE = str(False)


@receiver(COURSE_ENROLLMENT_CREATED)
def create_default_email_optin_on_enrollment(enrollment=None, **kwargs):  # pylint: disable=unused-argument
    """
    Record a default ``email-optin = False`` preference for the enrolled learner and the course's org.

    Only the UI enrollment flow (and API callers that pass ``email_opt_in``) record an opt-in choice, so learners
    enrolled any other way end up with no record and show as "unknown" in the email_opt_in_list report.

    An existing preference is never overwritten. Any failure is logged and swallowed so it can never block an
    enrollment.
    """
    if enrollment is None or not ENABLE_DEFAULT_EMAIL_OPTIN_ON_ENROLLMENT.is_enabled():
        return

    try:
        user_id = enrollment.user.id
        org = enrollment.course.course_key.org
        # Use a savepoint so a database error here can't break an enclosing enrollment transaction.
        with transaction.atomic():
            _, created = UserOrgTag.objects.get_or_create(
                user_id=user_id,
                org=org,
                key=EMAIL_OPTIN_KEY,
                defaults={'value': DEFAULT_EMAIL_OPTIN_VALUE},
            )
    except Exception:  # pylint: disable=broad-except
        log.exception(
            "Failed to record default email opt-in preference for enrollment [%s].",
            enrollment,
        )
        return

    if created:
        log.info(
            "Recorded default email opt-in preference [%s] for user [%s] and org [%s].",
            DEFAULT_EMAIL_OPTIN_VALUE, user_id, org,
        )
