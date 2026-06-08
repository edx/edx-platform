"""
Command to trigger sending reminder emails for learners to achieve their Course Goals
"""
import string
import time
from datetime import date, datetime, timedelta

import boto3
import pytz
from edx_ace.channel.django_email import DjangoEmailChannel
from edx_ace.channel.mixins import EmailChannelMixin
from eventtracking import tracker
import logging
import uuid

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.db.models import CharField, Count, Exists, F, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
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
# Number of goals to process per chunk. Keeps memory stable and allows
# bulk-fetching enrollments once per chunk instead of once per goal.
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
        """
        Handle goal emails across all courses

        Helpful notes for the function:
            weekday() returns an int 0-6 with Monday being 0 and Sunday being 6

        Performance notes:
            - Timezones are pre-filtered at the database level to only fetch goals
              where the user's local time is between 8 AM and 6 PM.  This is done
              by annotating each goal with the user's resolved timezone string
              (UserPreference → LastSeenCoursewareTimezone → 'UTC') and filtering
              against the set of timezone names that are currently in the 8-18 hour
              window.  This typically reduces the result set by 60-90%.
            - Ended-course exclusion is expressed as an inline Exists subquery on
              CourseOverview, avoiding a separate distinct + count round-trip.
            - Goals are processed in chunks of CHUNK_SIZE.  For each chunk, active
              enrollments are bulk-fetched in one query and seeded into
              RequestCache('get_enrollment') so that all downstream calls to
              CourseEnrollment.get_enrollment() hit the cache instead of the DB.
            - Expensive COUNT queries that previously took 3-4 minutes each on the
              annotated queryset have been removed.  Totals are accumulated in Python.
        """
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

        # The weekdays are 0 indexed, but we want this to be 1 to match required_days_left.
        # Essentially, if today is Sunday, days_left_in_week should be 1 since they have Sunday to hit their goal.
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

        # Subquery: resolve the user's explicit timezone preference (UserPreference key='time_zone')
        user_tz_pref_subquery = Subquery(
            UserPreference.objects.filter(
                user=OuterRef('user'),
                key='time_zone',
            ).values('value')[:1],
            output_field=CharField(),
        )

        # Subquery: fallback to the user's last-seen courseware timezone
        last_seen_tz_subquery = Subquery(
            LastSeenCoursewareTimezone.objects.filter(
                user=OuterRef('user'),
            ).values('last_seen_courseware_timezone')[:1],
            output_field=CharField(),
        )

        # Compute the set of pytz common timezone names where the local hour is
        # currently in the active window (8:00 AM inclusive – 6:00 PM exclusive).
        # This list is computed once and reused for the entire run; it has at most
        # ~440 entries (len(pytz.common_timezones)) and the computation is O(n) in
        # Python so it is essentially free.
        now_utc = datetime.now(pytz.utc)
        active_timezones = [
            tz_name for tz_name in pytz.common_timezones
            if 8 <= now_utc.astimezone(pytz.timezone(tz_name)).hour < 18
        ]

        # Only include goals where the user needs exactly days_left_in_week more days to hit their goal,
        # i.e. required_days_left == days_left_in_week, i.e. days_per_week - week_activity_count == days_left_in_week
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
            week_activity_count=F('days_per_week') - days_left_in_week,
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
            # Exclude all courses whose end dates are earlier than Sunday so we don't send an email about hitting
            # a course goal when it may not even be possible. Expressed as an inline Exists to avoid a separate
            # distinct-count round-trip that previously cost ~2 minutes of wall-time.
            Exists(
                CourseOverview.objects.filter(
                    id=OuterRef('course_key'),
                    end__date__lte=sunday_date,
                )
            )
        ).annotate(
            # Resolve each user's timezone at the DB level so we can filter to only
            # goals where the local hour is in the active window, avoiding a per-goal
            # call to get_user_timezone_or_last_seen_timezone_or_utc() (2 DB queries each).
            user_timezone_str=Coalesce(user_tz_pref_subquery, last_seen_tz_subquery, Value('UTC')),
        ).filter(
            user_timezone_str__in=active_timezones,
        ).select_related('user').order_by('user')

        tracker.emit(
            'edx.course.goal.email.session_started',
            {
                'uuid': session_id,
                'timestamp': datetime.now(),
                # goal_count is omitted here; a COUNT(*) on the fully-annotated
                # queryset previously took 3-4 minutes. The final count is emitted
                # accurately in session_completed below.
                'goal_count': None,
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
            # Pre-fetch all enrollments for this chunk in one query and seed
            # RequestCache so downstream get_enrollment() calls are cache hits.
            self._prefetch_enrollments_into_cache(chunk)

            for goal in chunk:
                # emulate a request for waffle's benefit
                with emulate_http_request(site=site, user=goal.user):
                    if self.handle_goal(goal, today, sunday_date, monday_date, session_id):
                        sent_count += 1
                    else:
                        filtered_count += 1

            # Clear the enrollment cache after each chunk to prevent memory growth.
            RequestCache('get_enrollment').clear()

            total_processed = sent_count + filtered_count
            if total_processed % 10000 == 0:
                log.info(
                    'Processing course goals: sent %d filtered %d total %d, timestamp: %s, uuid: %s',
                    sent_count, filtered_count, total_processed, datetime.now(), session_id,
                )

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
            sent_count, filtered_count, datetime.now(), session_id,
        )

    @staticmethod
    def _iter_chunks(queryset, chunk_size):
        """
        Yield successive list chunks from a queryset by buffering a server-side iterator.

        We avoid offset-based slicing because the queryset excludes goals that have
        already been marked email_reminder_sent=True.  As goals are processed and
        marked within the same run, the live result set shrinks, which would cause
        fixed offsets to skip rows.  Using queryset.iterator() opens a single
        server-side cursor that streams rows sequentially, making it immune to
        those mid-run writes.  The Python-side buffer gives us a concrete list per
        chunk so we can bulk-fetch enrollments before processing any goal in it.
        """
        chunk = []
        for item in queryset.iterator(chunk_size=chunk_size):
            chunk.append(item)
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = []
        if chunk:
            yield chunk

    @staticmethod
    def _prefetch_enrollments_into_cache(goals):
        """
        Bulk-fetch active enrollments for a list of goals and populate
        RequestCache('get_enrollment') so that subsequent calls to
        CourseEnrollment.get_enrollment(user, course_key, select_related=['course'])
        are satisfied from cache without hitting the database.

        This eliminates one DB query per goal (previously the dominant cost inside
        handle_goal) by replacing N individual lookups with a single IN-query.
        """
        if not goals:
            return

        user_ids = list({goal.user.id for goal in goals})
        course_keys = list({goal.course_key for goal in goals})

        enrollments = CourseEnrollment.objects.filter(
            user_id__in=user_ids,
            course_id__in=course_keys,
        ).select_related('course')

        enrollment_map = {(e.user_id, e.course_id): e for e in enrollments}
        request_cache = RequestCache('get_enrollment')

        for goal in goals:
            enrollment = enrollment_map.get((goal.user.id, goal.course_key))
            # Seed both cache-key variants used by get_enrollment():
            #   (user_id, course_key)              — called without select_related
            #   (user_id, course_key, 'course')    — called with select_related=['course']
            request_cache.set((goal.user.id, goal.course_key), enrollment)
            request_cache.set((goal.user.id, goal.course_key, 'course'), enrollment)

    @staticmethod
    def handle_goal(goal, today, sunday_date, _monday_date, session_id):
        """Sends an email reminder for a single CourseGoal, if it passes all our checks.

        Note: enrollment validity, certificate status, weekly activity count, course
        expiry, and timezone window are pre-filtered at the queryset level in
        _handle_all_goals. This method handles the remaining checks that cannot be
        efficiently expressed as DB filters (waffle flags, audit access expiration).

        The user's timezone string is available as goal.user_timezone_str (annotated
        by the queryset) so we avoid calling get_user_timezone_or_last_seen_timezone_or_utc()
        here (which would run 2 additional DB queries per goal).

        The enrollment object is already seeded into RequestCache by
        _prefetch_enrollments_into_cache(), so the get_enrollment() call below is a
        cache hit with no DB round-trip.
        """
        # Resolve timezone from the annotated field — no DB query needed.
        # Sanitize to remove any non-printable characters (rare but observed in production).
        user_timezone_str = ''.join(
            c for c in getattr(goal, 'user_timezone_str', 'UTC') if c in string.printable
        )
        try:
            user_timezone = pytz.timezone(user_timezone_str)
        except pytz.UnknownTimeZoneError:
            user_timezone = pytz.utc

        # Safety-net timezone check: the queryset already filters on active_timezones,
        # but the window may have shifted slightly between query build and processing.
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

        # Fetch enrollment — hits RequestCache seeded by _prefetch_enrollments_into_cache(),
        # so this is a cache lookup, not a DB query.
        enrollment = CourseEnrollment.get_enrollment(goal.user, goal.course_key, select_related=['course'])
        if not enrollment:
            return False

        audit_access_expiration_date = get_user_course_expiration_date(goal.user, enrollment.course_overview)
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
