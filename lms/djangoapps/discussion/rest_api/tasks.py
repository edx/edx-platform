"""
Contain celery tasks
"""

import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from edx_django_utils.monitoring import set_code_owner_attribute, set_custom_attribute
from eventtracking import tracker
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.track import segment
from lms.djangoapps.courseware.courses import get_course_with_access
from lms.djangoapps.discussion.django_comment_client.utils import get_user_role_names
from lms.djangoapps.discussion.rest_api.discussions_notifications import (
    DiscussionNotificationSender,
)
from lms.djangoapps.discussion.rest_api.utils import can_user_notify_all_learners
from openedx.core.djangoapps.django_comment_common.comment_client import Comment
from openedx.core.djangoapps.django_comment_common.comment_client.thread import Thread
from openedx.core.djangoapps.notifications.config.waffle import ENABLE_NOTIFICATIONS

User = get_user_model()
log = logging.getLogger(__name__)


@shared_task
@set_code_owner_attribute
def send_thread_created_notification(
    thread_id, course_key_str, user_id, notify_all_learners=False
):
    """
    Send notification when a new thread is created
    """
    course_key = CourseKey.from_string(course_key_str)
    if not ENABLE_NOTIFICATIONS.is_enabled(course_key):
        return
    thread = Thread(id=thread_id).retrieve()
    user = User.objects.get(id=user_id)

    if notify_all_learners:
        is_course_staff = CourseStaffRole(course_key).has_user(user)
        is_course_admin = CourseInstructorRole(course_key).has_user(user)
        user_roles = get_user_role_names(user, course_key)
        if not can_user_notify_all_learners(
            user_roles, is_course_staff, is_course_admin
        ):
            return

    course = get_course_with_access(user, "load", course_key, check_if_enrolled=True)
    notification_sender = DiscussionNotificationSender(thread, course, user)
    notification_sender.send_new_thread_created_notification(notify_all_learners)


@shared_task
@set_code_owner_attribute
def send_response_notifications(
    thread_id, course_key_str, user_id, comment_id, parent_id=None
):
    """
    Send notifications to users who are subscribed to the thread.
    """
    course_key = CourseKey.from_string(course_key_str)
    if not ENABLE_NOTIFICATIONS.is_enabled(course_key):
        return
    thread = Thread(id=thread_id).retrieve()
    user = User.objects.get(id=user_id)
    course = get_course_with_access(user, "load", course_key, check_if_enrolled=True)
    notification_sender = DiscussionNotificationSender(
        thread, course, user, parent_id, comment_id
    )
    notification_sender.send_new_comment_notification()
    notification_sender.send_new_response_notification()
    notification_sender.send_new_comment_on_response_notification()
    notification_sender.send_response_on_followed_post_notification()


@shared_task
@set_code_owner_attribute
def send_response_endorsed_notifications(
    thread_id, response_id, course_key_str, endorsed_by
):
    """
    Send notifications when a response is marked answered/ endorsed
    """
    course_key = CourseKey.from_string(course_key_str)
    if not ENABLE_NOTIFICATIONS.is_enabled(course_key):
        return
    thread = Thread(id=thread_id).retrieve()
    response = Comment(id=response_id).retrieve()
    creator = User.objects.get(id=response.user_id)
    endorser = User.objects.get(id=endorsed_by)
    course = get_course_with_access(creator, "load", course_key, check_if_enrolled=True)
    notification_sender = DiscussionNotificationSender(
        thread, course, creator, comment_id=response_id
    )
    # skip sending notification to author of thread if they are the same as the author of the response
    if response.user_id != thread.user_id:
        # sends notification to author of thread
        notification_sender.send_response_endorsed_on_thread_notification()
    # sends notification to author of response
    if int(response.user_id) != endorser.id:
        notification_sender.creator = User.objects.get(id=response.user_id)
        notification_sender.send_response_endorsed_notification()


@shared_task(
    bind=True,  # Enable retry context and access to task instance
    max_retries=3,  # Retry up to 3 times on failure
    default_retry_delay=60,  # Wait 60 seconds between retries
    autoretry_for=(OSError, TimeoutError),  # Only retry on transient network/IO errors
    retry_backoff=True,  # Exponential backoff between retries
    retry_jitter=True,   # Add randomization to retry delays
)
@set_code_owner_attribute
def delete_course_post_for_user(  # pylint: disable=too-many-statements
    self,
    user_id,
    username=None,
    course_ids=None,
    event_data=None,
    # NEW PARAMETERS (backward compatible - all have defaults):
    ban_user=False,
    ban_scope='course',
    moderator_id=None,
    reason=None,
):
    """
    Delete all discussion posts for a user and optionally ban them.

    BACKWARD COMPATIBLE: Existing callers without ban_user parameter
    will experience no change in behavior.

    Args:
        self: Task instance (when bind=True)
        user_id: User whose posts to delete
        username: Username of the user (optional, will be fetched if not provided)
        course_ids: List of course IDs (API sends single course wrapped in array)
        event_data: Event tracking metadata
        ban_user: If True, create ban record (NEW)
        ban_scope: 'course' or 'organization' (NEW)
        moderator_id: Moderator applying ban (NEW)
        reason: Ban reason (NEW)
    """
    event_data = event_data or {}
    if event_data.get("course_key"):
        set_custom_attribute("forum.operation", "bulk_delete_user_posts.execute")
        set_custom_attribute("forum.entity_type", "user")
        set_custom_attribute("forum.entity_id", str(user_id or ""))
        set_custom_attribute("forum.actor_id", str(event_data.get("triggered_by_user_id", "")))
        set_custom_attribute("forum.course_id", str(event_data.get("course_key", "")))
        set_custom_attribute("forum.scope", str(event_data.get("course_or_org", "")))
        set_custom_attribute("forum.course_count", str(len(course_ids or [])))
    log.info(
        f"<<Bulk Delete>> Deleting all posts for {username} in course {course_ids}"
    )
    # Get triggered_by user_id from event_data for audit trail
    deleted_by_user_id = event_data.get("triggered_by_user_id") if event_data else None
    threads_deleted = Thread.delete_user_threads(
        user_id, course_ids, deleted_by=deleted_by_user_id
    )
    comments_deleted = Comment.delete_user_comments(
        user_id, course_ids, deleted_by=deleted_by_user_id
    )
    if event_data.get("course_key"):
        set_custom_attribute("forum.threads_deleted", str(threads_deleted))
        set_custom_attribute("forum.comments_deleted", str(comments_deleted))
        set_custom_attribute("forum.result", "success")
    log.info(
        f"<<Bulk Delete>> Deleted {threads_deleted} posts and {comments_deleted} comments for {username} "
        f"in course {course_ids}"
    )

    # Create ban record if requested
    ban_id = None
    ban_error = None
    if ban_user:
        try:
            from forum import api as forum_api

            # Get user objects
            target_user = User.objects.get(id=user_id)
            moderator = User.objects.get(id=moderator_id) if moderator_id else None

            # Parse course key
            course_key = CourseKey.from_string(course_ids[0]) if course_ids else None

            # Create ban using forum API
            ban_result = forum_api.ban_user(
                user=target_user,
                banned_by=moderator,
                course_id=course_key,
                scope=ban_scope,
                reason=reason or "Bulk delete and ban operation"
            )

            ban_id = ban_result.get('id')

            log.info(
                f"<<Bulk Delete>> Created {ban_scope}-level ban (ID: {ban_id}) "
                f"for user {username} (ID: {user_id}) after deleting {threads_deleted + comments_deleted} items"
            )

            # Send escalation email (non-blocking)
            try:
                from lms.djangoapps.discussion.rest_api.emails import send_ban_escalation_email

                send_ban_escalation_email(
                    banned_user_id=user_id,
                    moderator_id=moderator_id,
                    course_id=course_ids[0] if course_ids else None,
                    scope=ban_scope,
                    reason=reason,
                    threads_deleted=threads_deleted,
                    comments_deleted=comments_deleted,
                )
            except Exception as email_exc:  # pylint: disable=broad-except
                log.error(
                    "<<Bulk Delete>> Failed to send ban escalation email for user %s (ID: %s): %s",
                    username,
                    user_id,
                    email_exc,
                    exc_info=True,
                )

        except Exception as e:  # pylint: disable=broad-except
            ban_error = str(e)
            log.error(
                f"<<Bulk Delete>> Failed to create ban for user {username} (ID: {user_id}): {e}",
                exc_info=True
            )
            # Don't fail the entire task if ban creation fails
            # Discussions are already deleted, so we log the error and continue

    event_data.update(
        {
            "number_of_posts_deleted": threads_deleted,
            "number_of_comments_deleted": comments_deleted,
            "ban_user": ban_user,
            "ban_scope": ban_scope if ban_user else None,
            "ban_id": ban_id if ban_user else None,
            "ban_error": ban_error if ban_error else None,
        }
    )
    event_name = "edx.discussion.bulk_delete_user_posts"
    tracker.emit(event_name, event_data)
    segment.track("None", event_name, event_data)

    # Return task result for monitoring
    return {
        "threads_deleted": threads_deleted,
        "comments_deleted": comments_deleted,
        "ban_created": bool(ban_id),
        "ban_id": ban_id,
        "ban_error": ban_error,
    }


@shared_task
@set_code_owner_attribute
def restore_course_post_for_user(user_id, username, course_ids, event_data=None):
    """
    Restores all soft-deleted posts for user in a course by setting is_deleted=False.
    """
    event_data = event_data or {}
    if event_data.get("course_key"):
        set_custom_attribute("forum.operation", "bulk_restore_user_posts.execute")
        set_custom_attribute("forum.entity_type", "user")
        set_custom_attribute("forum.entity_id", str(user_id or ""))
        set_custom_attribute("forum.actor_id", str(event_data.get("triggered_by_user_id", "")))
        set_custom_attribute("forum.course_id", str(event_data.get("course_key", "")))
        set_custom_attribute("forum.scope", str(event_data.get("course_or_org", "")))
        set_custom_attribute("forum.course_count", str(len(course_ids or [])))
    log.info(
        "<<Bulk Restore>> Restoring all posts for %s in course %s", username, course_ids
    )
    # Get triggered_by user_id from event_data for audit trail
    restored_by_user_id = event_data.get("triggered_by_user_id") if event_data else None
    threads_restored = Thread.restore_user_deleted_threads(
        user_id, course_ids, restored_by=restored_by_user_id
    )
    comments_restored = Comment.restore_user_deleted_comments(
        user_id, course_ids, restored_by=restored_by_user_id
    )
    if event_data.get("course_key"):
        set_custom_attribute("forum.threads_restored", str(threads_restored))
        set_custom_attribute("forum.comments_restored", str(comments_restored))
        set_custom_attribute("forum.result", "success")
    log.info(
        "<<Bulk Restore>> Restored %s posts and %s comments for %s in course %s",
        threads_restored,
        comments_restored,
        username,
        course_ids,
    )
    event_data.update(
        {
            "number_of_posts_restored": threads_restored,
            "number_of_comments_restored": comments_restored,
        }
    )
    event_name = "edx.discussion.bulk_restore_user_posts"
    tracker.emit(event_name, event_data)
    segment.track("None", event_name, event_data)
