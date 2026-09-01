"""
Celery task for course enrollment email
"""
import logging
from datetime import datetime

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from edx_django_utils.monitoring import set_code_owner_attribute
from edx_toggles.toggles import WaffleFlag
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.track import segment
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.helpers import (
    get_course_dates_for_email,
    get_instructors,
)
from lms.djangoapps.utils import get_email_client
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.catalog.utils import (
    get_course_uuid_for_course,
    get_owners_for_course,
    get_course_run_details,
)
from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.lib.celery.task_utils import emulate_http_request
from openedx.features.course_experience import ENABLE_COURSE_GOALS

User = get_user_model()
log = logging.getLogger(__name__)

MAX_RETRIES = 3

# .. toggle_name: student.enable_ses_for_course_enrollment
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Route course enrollment emails via SES (Django email backend) instead of Braze.
# .. toggle_use_cases: opt_in, temporary
# .. toggle_creation_date: 2026-06-01
# .. toggle_target_removal_date: None
# .. toggle_warning: When enabled, enrollment emails are sent via SES; Braze canvas message is skipped.
ENABLE_SES_FOR_COURSE_ENROLLMENT = WaffleFlag(
    'student.enable_ses_for_course_enrollment',
    __name__,
)


def _get_enrollment_email_language(user):
    """
    Return enrollment email language code supported by templates.

    Currently supports English and Spanish.
    """
    preferred_language = (UserPreference.get_value(user, LANGUAGE_KEY, default='en') or 'en').lower()
    if preferred_language.startswith('es'):
        return 'es'
    return 'en'


def _send_ses_enrollment_email(user, context, language='en'):
    """
    Send course enrollment email via SES using Django template rendering.

    Renders the themed enrollment HTML template (resolved from edx-themes if a themed
    override exists, falling back to edx-platform) and sends via Django's email backend.

    Template resolution:
    1. If SES_ENROLLMENT_EMAIL_THEME is configured, try that theme first
    2. Otherwise, use DEFAULT_SITE_THEME resolution
    3. Fall back to platform defaults if theme template not found

    Args:
        user: Django User instance
        context (dict): Template context built by send_course_enrollment_email
        language (str): 'en' or 'es' — selects the correct template variant
    """
    # Use SES-specific theme if configured, otherwise use default theme resolution
    ses_theme = getattr(settings, 'SES_ENROLLMENT_EMAIL_THEME', None)
    if ses_theme:
        # Attempt to load from SES-specific theme (e.g., "edx.org-next")
        template_name = f"{ses_theme}/emails/enrollment_{language}.html"
    else:
        # Use default theme resolution (DEFAULT_SITE_THEME or platform default)
        template_name = f"emails/enrollment_{language}.html"

    # Work on a copy so we don't mutate the caller's dict
    ctx = dict(context)
    # Inject SES-specific absolute image URLs (Braze ignores these keys)
    ctx.update(_build_enrollment_email_image_urls(language=language))

    html_body = render_to_string(template_name, ctx)
    plain_text_body = strip_tags(html_body).strip()
    if not plain_text_body:
        course_title = ctx.get('course_title', '')
        plain_text_body = (
            f"You are enrolled in {course_title}."
            if course_title
            else "You are enrolled in a course on edX."
        )

    from_email = configuration_helpers.get_value(
        'email_from_address',
        configuration_helpers.get_value('ACTIVATION_EMAIL_FROM_ADDRESS', settings.DEFAULT_FROM_EMAIL),
    )
    reply_to_email = configuration_helpers.get_value('CONTACT_EMAIL', settings.DEFAULT_FEEDBACK_EMAIL)
    course_title = ctx.get('course_title', '')
    if language == 'es':
        subject = (
            f"Te has inscrito en {course_title}"
            if course_title
            else "Te has inscrito en un curso de edX"
        )
    else:
        subject = (
            f"You're enrolled in {course_title}"
            if course_title
            else "You're enrolled in an edX course"
        )

    email_kwargs = {
        'subject': subject,
        'body': plain_text_body,
        'from_email': from_email,
        'to': [user.email],
    }
    if reply_to_email:
        email_kwargs['reply_to'] = [reply_to_email]

    msg = EmailMultiAlternatives(**email_kwargs)
    msg.attach_alternative(html_body, 'text/html')
    msg.send(fail_silently=False)


def _send_braze_enrollment_email(user_id, canvas_entry_properties):
    """
    Send course enrollment email via Braze canvas API.

    Uses the existing Braze helper to send a canvas message. This is used both
    for direct Braze sends and as a fallback when SES delivery fails.
    """
    recipients = [{"external_user_id": user_id}]
    braze_client = get_email_client()
    if braze_client:
        braze_client.send_canvas_message(
            canvas_id=getattr(settings, 'BRAZE_COURSE_ENROLLMENT_CANVAS_ID', None),
            recipients=recipients,
            canvas_entry_properties=canvas_entry_properties,
        )


def _build_enrollment_email_image_urls(language='en'):
    """
    Build absolute URLs for enrollment email images (SES only).

    This function constructs full image URLs for SES (Simple Email Service)
    email delivery. These URLs are required because SES email clients cannot
    resolve relative paths or Django static file paths; they need absolute
    URLs. Braze does NOT use these image URL keys; they are ignored by Braze
    and only used when the SES path is enabled.

    Args:
        language (str): Language code ('en' or 'es') to select correct image variants

    Returns:
        dict: Mapping of image variable names to absolute URLs

    How it works:
    1. settings.LMS_ROOT_URL is evaluated at runtime (e.g., 'https://courses.edx.org')
    2. f-string interpolates this value into the path
    3. SES email client receives complete URL: 'https://courses.edx.org/static/images/enrollment_email/...'
    4. Browser/email client can fetch image directly without context about Django

    Example flow:
    - Python (settings): LMS_ROOT_URL = 'https://courses.edx.org'
    - f-string: f"{settings.LMS_ROOT_URL}/static/images/enrollment_email/person_icon_{language}.png"
    - Result: 'https://courses.edx.org/static/images/enrollment_email/person_icon_en.png'
    - SES email client: Makes HTTP GET to that URL, renders image
    """
    lms_root = configuration_helpers.get_value(
        "LMS_ROOT_URL", settings.LMS_ROOT_URL
    ).rstrip('/')

    # NOTE: Image URLs for SES templates.
    # Currently not used in Braze flow — will be enabled during SES migration.
    # Construct image URLs based on language
    image_urls = {
        'logo_url': f"{lms_root}/static/images/edx_logo.png",
        'you_are_enrolled_en': f"{lms_root}/static/images/enrollment_email/you_are_enrolled_en.png",
        'banner_default': f"{lms_root}/static/images/enrollment_email/banner_default.png",
        'timer_icon_en': f"{lms_root}/static/images/enrollment_email/timer_icon_en.png",
        'person_icon_en': f"{lms_root}/static/images/enrollment_email/person_icon_en.png",
        'dollar_icon_en': f"{lms_root}/static/images/enrollment_email/dollar_icon_en.png",
        'goal_idea_icon_en': f"{lms_root}/static/images/enrollment_email/goal_idea_icon_en.png",
        'flag_icon_pink_en': f"{lms_root}/static/images/enrollment_email/flag_icon_pink_en.png",
        'flag_icon_black_en_es': f"{lms_root}/static/images/enrollment_email/flag_icon_black_en_es.png",
        'flag_icon_orange_en': f"{lms_root}/static/images/enrollment_email/flag_icon_orange_en.png",
        'vertical_line_white_en': f"{lms_root}/static/images/enrollment_email/vertical_line_white_en.png",
        'vertical_line_orange_en': f"{lms_root}/static/images/enrollment_email/vertical_line_orange_en.png",
        'vertical_line_black_en': f"{lms_root}/static/images/enrollment_email/vertical_line_black_en.png",
        'community_illustration_en': f"{lms_root}/static/images/enrollment_email/community_illustration_en.png",
    }

    # If Spanish, override with Spanish variants
    if language == 'es':
        spanish_overrides = {
            # Same banner for both languages.
            'banner_default': f"{lms_root}/static/images/enrollment_email/banner_default.png",
            'arrow_icon_es': f"{lms_root}/static/images/enrollment_email/arrow_icon_es.png",
            'timer_icon_es': f"{lms_root}/static/images/enrollment_email/timer_icon_es.png",
            'person_icon_es': f"{lms_root}/static/images/enrollment_email/person_icon_es.png",
            'dollar_icon_es': f"{lms_root}/static/images/enrollment_email/dollar_icon_es.png",
            'flag_icon_white_es': f"{lms_root}/static/images/enrollment_email/flag_icon_white_es.png",
            'flag_icon_grey_es': f"{lms_root}/static/images/enrollment_email/flag_icon_grey_es.png",
            # Same black flag icon for both languages.
            'flag_icon_black_en_es': f"{lms_root}/static/images/enrollment_email/flag_icon_black_en_es.png",
            'calendar_icon_es': f"{lms_root}/static/images/enrollment_email/calendar_icon_es.png",
            'community_icon_es': f"{lms_root}/static/images/enrollment_email/community_icon_es.png",
            'slant_line_es': f"{lms_root}/static/images/enrollment_email/slant_line_es.png",
        }
        image_urls.update(spanish_overrides)

    return image_urls


@shared_task(bind=True, ignore_result=True)
@set_code_owner_attribute
def send_course_enrollment_email(
    self, user_id, course_id, course_title, short_description, course_ended, pacing_type, track_mode
):  # pylint: disable=too-many-statements
    """
    Send course enrollment email using Braze API.

    Email is configured as Braze canvas message. We get the canvas properties for the
    email from course discovery service.
    In case the course run call to discovery fails, we use the course details sent
    to the celery task in our email.
    """
    course_date_blocks, course_key, is_course_run_missing, is_course_date_missing = [], None, False, False
    course_run_fields = [
        "key",
        "title",
        "short_description",
        "marketing_url",
        "pacing_type",
        "min_effort",
        "max_effort",
        "weeks_to_complete",
        "enrollment_count",
        "image",
        "staff",
    ]
    canvas_entry_properties = {
        "course_title": course_title,
        "short_description": short_description,
        "pacing_type": pacing_type,
        "course_run_key": course_id,
        "course_price": CourseMode.min_course_price_for_currency(
            course_id=course_id, currency="USD"
        ),
        # Strip trailing slashes so template concatenation like
        # "{{ lms_base_url }}/courses/..." never produces a double slash.
        "lms_base_url": configuration_helpers.get_value(
            "LMS_ROOT_URL", settings.LMS_ROOT_URL
        ).rstrip('/'),
        "learning_base_url": configuration_helpers.get_value(
            "LEARNING_MICROFRONTEND_URL", settings.LEARNING_MICROFRONTEND_URL
        ).rstrip('/'),
        "track_mode": track_mode,
        "current_year": datetime.now().year,
    }

    try:
        user = User.objects.get(id=user_id)
        course_key = CourseKey.from_string(course_id)
        if not course_ended:
            course_date_blocks = get_course_dates_for_email(user, course_key, request=None)
    except Exception as err:  # pylint: disable=broad-except
        log.exception(f'[Enrollment email] Failed to get course dates with error: {err}')
        is_course_date_missing = True

    canvas_entry_properties.update(
        {
            "course_date_blocks": course_date_blocks,
            "goals_enabled": ENABLE_COURSE_GOALS.is_enabled(course_key),
            "user_name": user.get_full_name() or user.first_name or user.username,
        }
    )

    enrollment_language = _get_enrollment_email_language(user)

    # Inject absolute image URLs for SES rendering. Braze ignores these extra
    # keys; they're consumed only when the SES path is enabled.
    canvas_entry_properties.update(_build_enrollment_email_image_urls(language=enrollment_language))

    try:
        course_uuid = get_course_uuid_for_course(course_id)
        if course_uuid is None:
            raise ValueError('Missing course_uuid')

        owners = get_owners_for_course(course_uuid=course_uuid)
        course_run = get_course_run_details(course_id, course_run_fields)
        if not course_run:
            raise ValueError('Missing course_run')

        marketing_root_url = settings.MKTG_URLS.get("ROOT")
        instructors = get_instructors(course_run, marketing_root_url)
        enrollment_count = int(course_run.get("enrollment_count")) if course_run.get("enrollment_count") else 0
        canvas_entry_properties.update({
            "instructors": instructors,
            "instructors_count": "even" if len(instructors) % 2 == 0 else "odd",
            "min_effort": course_run.get("min_effort"),
            "max_effort": course_run.get("max_effort"),
            "weeks_to_complete": course_run.get("weeks_to_complete"),
            "learners_count": (
                "{:,}".format(enrollment_count) if enrollment_count > 100 else ""
            ),
            "banner_image_url": (
                course_run.get("image").get("src", "") if course_run.get("image") else ""
            ),
            "course_title": course_run.get("title"),
            "short_description": course_run.get("short_description"),
            "pacing_type": course_run.get("pacing_type"),
            "partner_image_url": owners[0].get("logo_image_url") if owners else "",
            "org_name": owners[0].get("name") if owners else "",
        })
    except Exception as err:  # pylint: disable=broad-except
        is_course_run_missing = True
        log.warning(
            f"[Course Enrollment] Course run call failed for user: {user_id} "
            f"course: {course_id} error: {err}"
        )

    if is_course_run_missing or is_course_date_missing:
        segment_properties = {
            'course_key': course_id,
            'is_course_run_missing': is_course_run_missing,
            'is_course_date_missing': is_course_date_missing,
        }
        segment.track(user_id, 'edx.course.enrollment.email.missingdata', segment_properties)

    use_ses = ENABLE_SES_FOR_COURSE_ENROLLMENT.is_enabled()
    sent_via = 'SES'

    if use_ses:
        # SES path: render the themed Django template and send via the Django email backend.
        # emulate_http_request sets the thread-local site so the themed template loader
        # can resolve templates from edx-themes (mirrors account-activation SES pattern).
        try:
            site = Site.objects.get_current()
            with emulate_http_request(site=site, user=user):
                _send_ses_enrollment_email(user, canvas_entry_properties, language=enrollment_language)
        except Exception:  # pylint: disable=broad-except
            log.warning(
                "SES send failed for %s, falling back to Braze canvas message",
                user.email,
                exc_info=True,
            )
            try:
                _send_braze_enrollment_email(user_id, canvas_entry_properties)
                sent_via = 'Braze'
            except Exception as braze_exc:  # pylint: disable=broad-except
                log.error(
                    'Unable to send enrollment email to %s, attempted via SES and Braze fallback',
                    user.email,
                    exc_info=True,
                )
                countdown = 60 * (self.request.retries + 1)
                raise self.retry(exc=braze_exc, countdown=countdown, max_retries=MAX_RETRIES)
    else:
        # Braze path (default): send canvas message via the Braze client.
        sent_via = 'Braze'
        try:
            _send_braze_enrollment_email(user_id, canvas_entry_properties)
        except Exception as exc:  # pylint: disable=broad-except
            log.error(
                'Unable to send enrollment email to %s via Braze',
                user.email,
                exc_info=True,
            )
            countdown = 60 * (self.request.retries + 1)
            raise self.retry(exc=exc, countdown=countdown, max_retries=MAX_RETRIES)

    log.info(
        'Course enrollment email for %s sent via %s',
        user.email,
        sent_via,
    )
