"""
Handlers for notifications
"""
import logging

from django.db import IntegrityError
from django.dispatch import receiver
from openedx_events.learning.signals import COURSE_ENROLLMENT_CREATED, USER_NOTIFICATION

from openedx.core.djangoapps.notifications.config.waffle import ENABLE_NOTIFICATIONS
from openedx.core.djangoapps.notifications.models import CourseNotificationPreference

log = logging.getLogger(__name__)


@receiver(COURSE_ENROLLMENT_CREATED)
def course_enrollment_post_save(signal, sender, enrollment, metadata):
    """
    Watches for post_save signal for creates on the CourseEnrollment table.
    Generate a CourseNotificationPreference if new Enrollment is created
    """
    if ENABLE_NOTIFICATIONS.is_enabled(enrollment.course.course_key):
        try:
            CourseNotificationPreference.objects.create(
                user_id=enrollment.user.id,
                course_id=enrollment.course.course_key
            )
        except IntegrityError:
            log.info(f'CourseNotificationPreference already exists for user {enrollment.user} '
                     f'and course {enrollment.course_id}')

@receiver(USER_NOTIFICATION)
def generate_user_notifications(signal, sender, notification_data, metadata):
    """
    Watches for USER_NOTIFICATION signal and calls  send_web_notifications task
    """
    from openedx.core.djangoapps.notifications.tasks import send_notifications
    send_notifications.delay(**notification_data)
