"""
Celery task for notifications app
"""
from celery import shared_task
from edx_django_utils.monitoring import set_code_owner_attribute

from openedx.core.djangoapps.notifications.models import CourseNotificationPreference


@shared_task
@set_code_owner_attribute
def send_notifications(user_ids, app_name, notification_type, context, content_url):
    """
    Send notifications to the users.
    """
    from .models import Notification
    user_ids = list(set(user_ids))
    course_id = context.get('course_id', None)
    # check if what is preferences of user and make decision to send notification or not
    preferences = CourseNotificationPreference.objects.filter(
        user_id__in=user_ids,
        course_id=course_id,
    )
    notifications = []
    for preference in preferences:
        if preference and preference.get_web_config(app_name, notification_type):
            notifications.append(Notification(
                user_id=preference.user_id,
                app_name=app_name,
                notification_type=notification_type,
                content_context=context,
                content_url=content_url,
                course_id=course_id,
            ))
    # send notification to users but use bulk_create
    Notification.objects.bulk_create(notifications)
