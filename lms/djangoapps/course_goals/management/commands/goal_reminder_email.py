"""
Command to trigger sending reminder emails for learners to achieve their Course Goals
"""
import logging
import string
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta

import boto3
import pytz
from edx_ace.channel.django_email import DjangoEmailChannel
from edx_ace.channel.mixins import EmailChannelMixin
from eventtracking import tracker

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.db.models import CharField, Count, Exists, F, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, NullIf
from edx_ace import ace, presentation
from edx_ace.message import Message
from edx_ace.recipient import Recipient
from edx_ace.utils.signals import send_ace_message_sent_signal
from edx_django_utils.cache import RequestCache

from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.models import GeneratedCertificate
from lms.djangoapps.courseware.models import LastSeenCoursewareTimezone
from lms.djangoapps.course_goals.models import CourseGoal, CourseGoalReminderStatus, UserActivity
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference
from openedx.core.lib.celery.task_utils import emulate_http_request
from openedx.features.course_duration_limits.access import get_user_course_expiration_date
from openedx.features.course_experience import ENABLE_COURSE_GOALS, ENABLE_SES_FOR_GOALREMINDER
from openedx.features.course_experience.url_helpers import get_learning_mfe_home_url

log = logging.getLogger(__name__)

MONDAY_WEEKDAY = 0
SUNDAY_WEEKDAY = 6
CHUNK_SIZE = 2000


def send_ace_message(goal, session_id):
    """
    Send an email reminding users to stay on track for their learning goal in this course

    Arguments:
        goal (CourseGoal): Goal object

    Returns true if sent, false if it absorbed an exception and did not send
    """
    user = goal.user
    if not user.has_usable_password():
        log.info(f'Goal Reminder User is disabled {user.username} course {goal.course_key}')
        return False
    try:
        course = CourseOverview.get_from_id(goal.course_key)
    except CourseOverview.DoesNotExist:
        log.error(f"Goal Reminder course {goal.course_key} not found.")
        tracker.emit(
            'edx.course.goal.email.failed',
            {
                'uuid': session_id,
                'timestamp': datetime.now(),
                'reason': 'course not found',
                'course_key': goal.course_key,
            }
        )
        return False

    course_name = course.display_name

    site = Site.objects.get_current()
    message_context = get_base_template_context(site)

    course_home_url = get_learning_mfe_home_url(course_key=goal.course_key, url_fragment='home')

    goals_unsubscribe_url = f'{settings.LEARNING_MICROFRONTEND_URL}/goal-unsubscribe/{goal.unsubscribe_token}'

    language = get_user_preference(user, LANGUAGE_KEY)

    # Code to allow displaying different banner images for different languages
    # However, we'll likely want to develop a better way to do this within edx-ace
    image_url = settings.STATIC_URL
    if image_url:
        # If the image url is a relative url prepend the LMS ROOT
        if 'http' not in image_url:
            image_url = settings.LMS_ROOT_URL + settings.STATIC_URL
        image_url += 'images/'

        if language and language in ['es', 'es-419']:
            image_url += 'spanish-'

    message_context.update({
        'email': user.email,
        'user_name': user.username,
        'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
        'course_name': course_name,
        'course_id': str(goal.course_key),
        'days_per_week': goal.days_per_week,
        'course_url': course_home_url,
        'goals_unsubscribe_url': goals_unsubscribe_url,
        'image_url': image_url,
        'unsubscribe_url': None,  # We don't want to include the default unsubscribe link
        'omit_unsubscribe_link': True,
        'courses_url': getattr(settings, 'ACE_EMAIL_COURSES_URL', None),
        'programs_url': getattr(settings, 'ACE_EMAIL_PROGRAMS_URL', None),
        'goal_reminder_banner_url': settings.GOAL_REMINDER_BANNER_URL,
        'goal_reminder_profile_url': settings.GOAL_REMINDER_PROFILE_URL,
    })

    options = {
        'transactional': True,
        'skip_disable_user_policy': True
    }

    is_ses_enabled = ENABLE_SES_FOR_GOALREMINDER.is_enabled(goal.course_key)

    if is_ses_enabled:
        options.update({
            'from_address': settings.LMS_COMM_DEFAULT_FROM_EMAIL,
            'override_default_channel': 'django_email',
        })

    msg = Message(
        name="goalreminder",
        app_label="course_goals",
        recipient=Recipient(user.id, user.email),
        language=language,
        context=message_context,
        options=options,
    )

    with emulate_http_request(site, user):
        try:
            start_time = time.perf_counter()
            if is_ses_enabled:
                # experimental implementation to log errors with ses
                send_email_using_ses(user, msg)
            else:
                ace.send(msg)
            end_time = time.perf_counter()
            log.info(f"Goal Reminder for {user.id} for course {goal.course_key} sent in {end_time - start_time} "
                     f"using {'SES' if is_ses_enabled else 'others'}")
        except Exception as exc:  # pylint: disable=broad-except
            log.error(f"Goal Reminder for {user.id} for course {goal.course_key} could not send: {exc}")
            tracker.emit(
                'edx.course.goal.email.failed',
                {
                    'uuid': session_id,
                    'timestamp': datetime.now(),
                    'reason': 'ace error',
                    'error': str(exc),
                }
            )
            return False
    return True


class Command(BaseCommand):
    """
    Example usage:
        $ ./manage.py lms goal_reminder_email
    """
    help = 'Send emails to users that are in danger of missing their course goals for the week'

    def handle(self, *args, **options):
        """
        Handle goal emails across all courses.

        This outer layer calls the inner and reports on any exception that
        occurred.
        """

        try:
            self._handle_all_goals()
        except BaseException as exc:  # pylint: disable=broad-except
            log.exception("Error while sending course goals emails: ")
            tracker.emit(
                'edx.course.goal.email.failed',
                {
                    'timestamp': datetime.now(),
                    'reason': 'base exception',
                    'error': str(exc),
                }
            )
            for h in log.handlers:
                h.flush()
            raise

    def _handle_all_goals(self):
        """Handle goal emails across all courses."""
        today = date.today()
        sunday_date = today + timedelta(days=SUNDAY_WEEKDAY - today.weekday())
        monday_date = today - timedelta(days=today.weekday())
        session_id = str(uuid.uuid4())

        # Monday is the start of when we consider user's activity towards counting towards their weekly
        # goal. As such, we use Mondays to clear out the email reminders sent from the previous week.
        if today.weekday() == MONDAY_WEEKDAY:
            CourseGoalReminderStatus.objects.filter(email_reminder_sent=True).update(email_reminder_sent=False)
            log.info('Cleared all reminder statuses')
            return

        course_goals = self._get_course_goals(today, sunday_date, monday_date)
        sent_count, filtered_count = self._process_course_goals(course_goals, sunday_date, session_id)

        tracker.emit(
            'edx.course.goal.email.session_completed',
            {
                'uuid': session_id,
                'timestamp': datetime.now(),
                'goal_count': sent_count + filtered_count,
                'emails_sent': sent_count,
                'emails_filtered': filtered_count,
            }
        )
        log.info(
            'Processing course goals complete: sent %d emails, filtered out %d emails, timestamp: %s, uuid: %s',
            sent_count,
            filtered_count,
            datetime.now(),
            session_id,
        )

    @staticmethod
    def _get_course_goals(today, sunday_date, monday_date):
        """Build the queryset of goals eligible for reminders during this run."""
        days_left_in_week = SUNDAY_WEEKDAY - today.weekday() + 1

        active_enrollment_exists = CourseEnrollment.objects.filter(
            user=OuterRef('user'),
            course_id=OuterRef('course_key'),
            is_active=True,
            created__date__lt=monday_date,
        )

        # Subquery: count how many activity days this user has logged this week for this course
        week_activity_subquery = Coalesce(
            Subquery(
                UserActivity.objects.filter(
                    user=OuterRef('user'),
                    course_key=OuterRef('course_key'),
                    date__gte=monday_date,
                ).values('user', 'course_key').annotate(cnt=Count('pk')).values('cnt'),
                output_field=IntegerField(),
            ),
            Value(0),
        )

        user_tz_pref_subquery = Subquery(
            UserPreference.objects.filter(
                user=OuterRef('user'),
                key='time_zone',
            ).values('value')[:1],
            output_field=CharField(),
        )
        last_seen_tz_subquery = Subquery(
            LastSeenCoursewareTimezone.objects.filter(
                user=OuterRef('user'),
            ).values('last_seen_courseware_timezone')[:1],
            output_field=CharField(),
        )

        now_utc = datetime.now(pytz.utc)
        active_timezones = [
            tz_name
            for tz_name in pytz.common_timezones
            if 8 <= now_utc.astimezone(pytz.timezone(tz_name)).hour < 18
        ]

        # Only include goals where the user needs exactly days_left_in_week more days to hit their goal.
        # Keep the unsigned days_per_week field on the non-subtracted side because MySQL raises an
        # out-of-range error when days_per_week is less than days_left_in_week.
        course_goals = CourseGoal.objects.filter(
            days_per_week__gt=0,
            subscribed_to_reminders=True,
        ).filter(
            Exists(active_enrollment_exists)
        ).exclude(
            reminder_status__email_reminder_sent=True,
        ).annotate(
            week_activity_count=week_activity_subquery,
        ).filter(
            days_per_week=F('week_activity_count') + days_left_in_week,
        ).exclude(
            # Exclude users who already have a downloadable certificate — they've completed the course
            Exists(
                GeneratedCertificate.objects.filter(
                    user=OuterRef('user'),
                    course_id=OuterRef('course_key'),
                    status=CertificateStatuses.downloadable,
                )
            )
        ).exclude(
            Exists(
                CourseOverview.objects.filter(
                    id=OuterRef('course_key'),
                    end__date__lte=sunday_date,
                )
            )
        ).annotate(
            # NullIf preserves the existing Python fallback behavior for empty preferences.
            user_timezone_str=Coalesce(
                NullIf(user_tz_pref_subquery, Value('')),
                NullIf(last_seen_tz_subquery, Value('')),
                Value('UTC'),
            ),
        ).filter(
            # Unrecognized values must reach handle_goal so it can sanitize them or fall back to UTC.
            Q(user_timezone_str__in=active_timezones)
            | ~Q(user_timezone_str__in=pytz.common_timezones)
        ).select_related('user')

        return course_goals

    def _process_course_goals(self, course_goals, sunday_date, session_id):
        """Send reminders for an eligible queryset and return sent and filtered counts."""
        tracker.emit(
            'edx.course.goal.email.session_started',
            {
                'uuid': session_id,
                'timestamp': datetime.now(),
            }
        )
        log.info(
            'Processing course goals started, timestamp: %s, uuid: %s',
            datetime.now(),
            session_id,
        )
        site = Site.objects.get_current()
        sent_count = 0
        filtered_count = 0

        for chunk in self._iter_chunks(course_goals, CHUNK_SIZE):
            enrollment_map = self._prefetch_enrollments_into_cache(chunk)

            for goal in chunk:
                with emulate_http_request(site=site, user=goal.user):
                    if self.handle_goal(
                        goal,
                        enrollment_map.get((goal.user_id, goal.course_key)),
                        sunday_date,
                        session_id,
                    ):
                        sent_count += 1
                    else:
                        filtered_count += 1

            RequestCache('get_enrollment').clear()

            total_processed = sent_count + filtered_count
            if total_processed % 10000 == 0:
                log.info(
                    'Processing course goals: sent %d filtered %d total %d, timestamp: %s, uuid: %s',
                    sent_count,
                    filtered_count,
                    total_processed,
                    datetime.now(),
                    session_id,
                )

        return sent_count, filtered_count

    @staticmethod
    def _iter_chunks(queryset, chunk_size):
        """Yield stable primary-key chunks without offset pagination."""
        last_pk = 0
        while True:
            chunk = list(queryset.filter(pk__gt=last_pk).order_by('pk')[:chunk_size])
            if not chunk:
                return
            yield chunk
            last_pk = chunk[-1].pk

    @staticmethod
    def _prefetch_enrollments_into_cache(goals):
        """Fetch and cache only the enrollment pairs represented by this goal chunk."""
        if not goals:
            return {}

        users_by_course = defaultdict(set)
        for goal in goals:
            users_by_course[goal.course_key].add(goal.user_id)

        enrollment_filter = Q()
        for course_key, user_ids in users_by_course.items():
            enrollment_filter |= Q(course_id=course_key, user_id__in=user_ids)

        enrollments = CourseEnrollment.objects.filter(
            enrollment_filter,
            is_active=True,
        ).select_related('course')
        enrollment_map = {(enrollment.user_id, enrollment.course_id): enrollment for enrollment in enrollments}

        request_cache = RequestCache('get_enrollment')
        for goal in goals:
            enrollment = enrollment_map.get((goal.user_id, goal.course_key))
            request_cache.set((goal.user_id, goal.course_key), enrollment)
            request_cache.set((goal.user_id, goal.course_key, 'course'), enrollment)

        return enrollment_map

    @staticmethod
    def handle_goal(goal, enrollment, sunday_date, session_id):
        """Sends an email reminder for a single CourseGoal, if it passes all our checks.

        Note: enrollment validity, certificate status, and weekly activity count are pre-filtered
        at the queryset level in _handle_all_goals. This method handles the remaining checks that
        cannot be efficiently expressed as DB filters.
        """
        # Check timezone first — cheapest check, no DB query
        user_timezone_str = ''.join(
            character for character in getattr(goal, 'user_timezone_str', 'UTC') if character in string.printable
        )
        try:
            user_timezone = pytz.timezone(user_timezone_str)
        except pytz.UnknownTimeZoneError:
            user_timezone = pytz.utc
        now_in_users_timezone = datetime.now(user_timezone)
        if not 8 <= now_in_users_timezone.hour < 18:
            tracker.emit(
                'edx.course.goal.email.filtered',
                {
                    'uuid': session_id,
                    'timestamp': datetime.now(),
                    'reason': 'User time zone',
                    'user_timezone': str(user_timezone),
                    'now_in_users_timezone': now_in_users_timezone,
                }
            )
            return False

        if not ENABLE_COURSE_GOALS.is_enabled(goal.course_key):
            return False

        # Fetch enrollment only to check audit access expiration date
        if not enrollment:
            return False

        audit_access_expiration_date = get_user_course_expiration_date(
            goal.user,
            enrollment.course_overview,
            enrollment=enrollment,
        )
        # If an audit user's access expires this week, exclude them from the email since they may not
        # be able to hit their goal anyway
        if audit_access_expiration_date and audit_access_expiration_date.date() <= sunday_date:
            return False

        sent = send_ace_message(goal, session_id)
        if sent:
            CourseGoalReminderStatus.objects.update_or_create(goal=goal, defaults={'email_reminder_sent': True})
            return True

        return False


def send_email_using_ses(user, msg):
    """
    Send email using AWS SES
    """
    render_msg = presentation.render(DjangoEmailChannel, msg)
    # send rendered email using SES

    sender = EmailChannelMixin.get_from_address(msg)

    subject = EmailChannelMixin.get_subject(render_msg)
    body_text = render_msg.body
    body_html = render_msg.body_html

    try:
        # Send email
        response = boto3.client('ses', settings.AWS_SES_REGION_NAME).send_email(
            Source=sender,
            Destination={
                'ToAddresses': [user.email],
            },
            Message={
                'Subject': {
                    'Data': subject,
                    'Charset': 'UTF-8'
                },
                'Body': {
                    'Text': {
                        'Data': body_text,
                        'Charset': 'UTF-8'
                    },
                    'Html': {
                        'Data': body_html,
                        'Charset': 'UTF-8'
                    }
                }
            }
        )

        log.info(f"Goal Reminder Email: email sent using SES with message ID {response['MessageId']}")
        send_ace_message_sent_signal(DjangoEmailChannel, msg)
    except Exception as e:  # pylint: disable=broad-exception-caught
        log.error(f"Goal Reminder Email: Error sending email using SES: {e}")
        raise e
