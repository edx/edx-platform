"""
Celery task for notifications app
"""
from celery import shared_task
from edx_django_utils.monitoring import set_code_owner_attribute

from openedx.core.djangoapps.notifications.models import NotificationPreference


@shared_task
@set_code_owner_attribute
def send_notifications(user_ids, app_name, notification_type, context, content_url):
    """
    Send notifications to the users.
    """
    from .models import Notification
    user_ids = list(set(user_ids))
    # check if what is preferences of user and make decision to send notification or not
    preferences = NotificationPreference.objects.filter(
        user_id__in=user_ids,
        course_id=context.get('course_id', None),
    )
    preferences = {pref.user_id: pref for pref in preferences}
    notifications = []

    for user_id in user_ids:
        app_pref = preferences.get(user_id, None)
        # There might be no config for the app, notification_type, so we need to check for it
        if app_pref and app_pref.get_web_config(app_name, notification_type):
            notifications.append(Notification(
                user_id=user_id,
                app_name=app_name,
                notification_type=notification_type,
                content_context=context,
                content_url=content_url
            ))
    # send notification to users but use bulk_create
    Notification.objects.bulk_create(notifications)
