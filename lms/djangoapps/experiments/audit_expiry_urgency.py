"""Audit Expiry Urgency experiment (v1) helpers.

Implements enrollment-time assignment via Optimizely and persistence of a stable
`audit_expiry_at` datetime via CourseEnrollmentAttribute.

Design constraints (from ticket):
* Experiment key: audit_expiry_urgency_v1
* Variants: control_5_7_weeks, expiry_7_days
* Assignment unit: user_id
* Stickiness: across sessions/devices and across the configured target courses
* Failure behavior: default to control
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import MultipleObjectsReturned
from django.utils import timezone

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollmentAttribute
from lms.djangoapps.utils import OptimizelyClient
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.features.course_duration_limits.access import get_user_course_duration

from .flags import AUDIT_EXPIRY_URGENCY_V1_ENABLED

log = logging.getLogger(__name__)

# Avoid emitting a warning on every enrollment save if the allowlist is missing.
_WARNED_NO_TARGET_COURSES = False


EXPERIMENT_KEY = 'audit_expiry_urgency_v1'

VARIANT_CONTROL = 'control_5_7_weeks'
VARIANT_EXPIRY_7_DAYS = 'expiry_7_days'
VALID_VARIANTS = {VARIANT_CONTROL, VARIANT_EXPIRY_7_DAYS}

ATTRIBUTE_NAMESPACE = 'audit_expiry_experiment'
ATTRIBUTE_NAME_EXPERIMENT_KEY = 'experiment_key'
ATTRIBUTE_NAME_VARIANT = 'variant'
ATTRIBUTE_NAME_EXPIRY_DAYS = 'expiry_days'
ATTRIBUTE_NAME_ASSIGNED_AT = 'assigned_at'
ATTRIBUTE_NAME_DECISION_SOURCE = 'decision_source'
ATTRIBUTE_NAME_AUDIT_EXPIRY_AT = 'audit_expiry_at'

SITE_CONFIG_KEY_TARGET_COURSES = 'AUDIT_EXPIRY_EXPERIMENT_COURSES'

# TEMP: Local testing fallback.
# When set (e.g. in devstack), this forces a specific variant and skips the
# Optimizely lookup entirely.
FORCE_VARIANT_SETTING = 'AUDIT_EXPIRY_FORCE_VARIANT'


def _get_configured_target_course_id_strings():
    """Return configured target course run IDs as a list of strings."""
    default_from_settings = getattr(settings, SITE_CONFIG_KEY_TARGET_COURSES, [])
    configured = configuration_helpers.get_value(SITE_CONFIG_KEY_TARGET_COURSES, default=default_from_settings)

    if configured is None:
        configured = []
    if isinstance(configured, str):
        configured = [configured]
    if not isinstance(configured, list):
        configured = []

    course_ids = [str(item) for item in configured if item]
    if not course_ids:
        global _WARNED_NO_TARGET_COURSES  # pylint: disable=global-statement
        if not _WARNED_NO_TARGET_COURSES:
            log.warning('Audit expiry urgency: no target courses configured (key=%s)', SITE_CONFIG_KEY_TARGET_COURSES)
            _WARNED_NO_TARGET_COURSES = True
    return course_ids


def is_target_course(course_key):
    """Return True if course_key is one of the configured target course runs."""
    return str(course_key) in set(_get_configured_target_course_id_strings())


def _get_attribute(enrollment, name):
    """Get the latest CourseEnrollmentAttribute row for our namespace/name."""
    return enrollment.attributes.filter(namespace=ATTRIBUTE_NAMESPACE, name=name).order_by('id').last()


def get_persisted_variant(enrollment):
    """Return the persisted experiment variant for an enrollment, if present."""
    attr = _get_attribute(enrollment, ATTRIBUTE_NAME_VARIANT)
    return attr.value if attr else None


def get_persisted_experiment_key(enrollment):
    """Return the persisted experiment key for an enrollment, if present."""
    attr = _get_attribute(enrollment, ATTRIBUTE_NAME_EXPERIMENT_KEY)
    return attr.value if attr else None


def get_persisted_expiry_days(enrollment):
    """Return the persisted expiry-days value as an int, if valid."""
    attr = _get_attribute(enrollment, ATTRIBUTE_NAME_EXPIRY_DAYS)
    if not attr:
        return None
    try:
        return int(attr.value)
    except (TypeError, ValueError):
        return None


def get_persisted_assigned_at(enrollment):
    """Return the persisted assignment timestamp for an enrollment, if present."""
    attr = _get_attribute(enrollment, ATTRIBUTE_NAME_ASSIGNED_AT)
    return attr.value if attr else None


def get_persisted_decision_source(enrollment):
    """Return how the persisted experiment decision was made, if present."""
    attr = _get_attribute(enrollment, ATTRIBUTE_NAME_DECISION_SOURCE)
    return attr.value if attr else None


def get_persisted_audit_expiry_at(enrollment):
    """Return the persisted audit expiry datetime string for an enrollment, if present."""
    attr = _get_attribute(enrollment, ATTRIBUTE_NAME_AUDIT_EXPIRY_AT)
    return attr.value if attr else None


def _set_attribute(enrollment, name, value):
    """Set an enrollment attribute, tolerating duplicates."""
    try:
        CourseEnrollmentAttribute.objects.update_or_create(
            enrollment=enrollment,
            namespace=ATTRIBUTE_NAMESPACE,
            name=name,
            defaults={'value': value},
        )
    except MultipleObjectsReturned:
        # Duplicates are possible (no uniqueness constraint). Prefer updating the latest.
        existing = CourseEnrollmentAttribute.objects.filter(
            enrollment=enrollment,
            namespace=ATTRIBUTE_NAMESPACE,
            name=name,
        ).order_by('id').last()
        if existing:
            existing.value = value
            existing.save()
        else:
            CourseEnrollmentAttribute.objects.create(
                enrollment=enrollment,
                namespace=ATTRIBUTE_NAMESPACE,
                name=name,
                value=value,
            )


def _find_existing_variant_for_user(user, target_course_id_strings):
    """Reuse learner-level assignment by looking for an existing stored variant."""
    if not target_course_id_strings:
        return None

    # Note: do NOT filter on enrollment.is_active; we want reenrollments to retain assignment.
    existing_variant = (
        CourseEnrollmentAttribute.objects.filter(
            enrollment__user=user,
            enrollment__course_id__in=target_course_id_strings,
            namespace=ATTRIBUTE_NAMESPACE,
            name=ATTRIBUTE_NAME_VARIANT,
        )
        .order_by('-id')
        .values_list('value', flat=True)
        .first()
    )

    return existing_variant if existing_variant in VALID_VARIANTS else None


def _forced_variant_from_settings():
    """Return a forced variant if configured for local testing, else None."""
    forced = getattr(settings, FORCE_VARIANT_SETTING, None)
    if forced in VALID_VARIANTS:
        return forced
    if forced is not None:
        log.warning(
            'Audit expiry urgency: invalid %s=%r; ignoring (expected one of %s)',
            FORCE_VARIANT_SETTING,
            forced,
            sorted(VALID_VARIANTS),
        )
    return None


def _activate_optimizely_variant(user):
    """Return variant key from Optimizely activate(), or None on failure."""
    optimizely_client = OptimizelyClient.get_optimizely_client()
    if optimizely_client is None:
        return None
    try:
        variation_key = optimizely_client.activate(EXPERIMENT_KEY, str(user.id))
        return variation_key
    except Exception:  # pylint: disable=broad-except
        # Never break enrollment due to Optimizely issues.
        log.exception(
            'Audit expiry urgency: Optimizely activate failed for user_id=%s experiment=%s',
            user.id,
            EXPERIMENT_KEY,
        )
        return None


def choose_variant(user, target_course_id_strings):
    """Choose (or reuse) a learner-level variant and decision source."""
    forced_variant = _forced_variant_from_settings()
    if forced_variant:
        return forced_variant, 'force_variant'

    existing_variant = _find_existing_variant_for_user(user, target_course_id_strings)
    if existing_variant:
        return existing_variant, 'existing'

    variation_key = _activate_optimizely_variant(user)
    if variation_key in VALID_VARIANTS:
        return variation_key, 'optimizely'

    if variation_key is not None:
        log.warning('Audit expiry urgency: unexpected variation=%s; falling back to control', variation_key)
    else:
        log.warning('Audit expiry urgency: Optimizely unavailable; falling back to control')
    return VARIANT_CONTROL, 'fallback_control'


def _content_availability_date(enrollment):
    course_start = getattr(enrollment.course_overview, 'start', None)
    if course_start is None:
        return enrollment.created
    return max(enrollment.created, course_start)


def compute_audit_expiry_at(enrollment, variant, access_duration):
    """Compute the persisted audit expiry datetime for this enrollment."""
    content_availability_date = _content_availability_date(enrollment)
    if variant == VARIANT_EXPIRY_7_DAYS:
        expiry_at = content_availability_date + timedelta(days=7)
        return expiry_at, 7
    expiry_at = content_availability_date + access_duration
    return expiry_at, access_duration.days


def _track_exposure_event(user, course_key, variant, expiry_days, decision_source):
    """Track the first persisted learner assignment for this experiment."""
    try:
        optimizely_client = OptimizelyClient.get_optimizely_client()
        if optimizely_client:
            optimizely_client.track(
                'audit_expiry_urgency_exposed',
                str(user.id),
                attributes={
                    'experiment_key': EXPERIMENT_KEY,
                    'variant': variant,
                    'expiry_days': expiry_days,
                    'course_id': str(course_key),
                    'decision_source': decision_source,
                }
            )
    except Exception:  # pylint: disable=broad-except
        log.exception('Audit expiry urgency: failed to track exposure for user_id=%s', user.id)


def maybe_persist_audit_expiry_urgency_attributes(enrollment):
    """Persist variant and audit_expiry_at for an eligible enrollment.

    Safe + idempotent: if audit_expiry_at already exists, this is a no-op.
    """
    if not AUDIT_EXPIRY_URGENCY_V1_ENABLED.is_enabled():
        return

    if not enrollment or not getattr(enrollment, 'user_id', None):
        log.warning('Audit expiry urgency: skipped (missing enrollment or user)')
        return

    if not enrollment.course_overview:
        log.warning('Audit expiry urgency: skipped (missing course_overview)')
        return

    if any((
        not enrollment.is_active,
        enrollment.mode != CourseMode.AUDIT,
        not is_target_course(enrollment.course_id),
    )):
        return

    # Idempotency: do not overwrite once set.
    if get_persisted_audit_expiry_at(enrollment):
        return

    access_duration = get_user_course_duration(enrollment.user, enrollment.course_overview)
    if access_duration is None:
        # Only apply if Course Duration Limits would normally apply.
        return

    target_course_ids = _get_configured_target_course_id_strings()
    variant, decision_source = choose_variant(enrollment.user, target_course_ids)

    audit_expiry_at, expiry_days = compute_audit_expiry_at(enrollment, variant, access_duration)
    if timezone.is_naive(audit_expiry_at):
        audit_expiry_at = timezone.make_aware(audit_expiry_at, timezone=timezone.utc)

    assigned_at = timezone.now()

    _set_attribute(enrollment, ATTRIBUTE_NAME_EXPERIMENT_KEY, EXPERIMENT_KEY)
    _set_attribute(enrollment, ATTRIBUTE_NAME_VARIANT, variant)
    _set_attribute(enrollment, ATTRIBUTE_NAME_EXPIRY_DAYS, str(expiry_days))
    _set_attribute(enrollment, ATTRIBUTE_NAME_ASSIGNED_AT, assigned_at.isoformat())
    _set_attribute(enrollment, ATTRIBUTE_NAME_DECISION_SOURCE, decision_source)
    _set_attribute(enrollment, ATTRIBUTE_NAME_AUDIT_EXPIRY_AT, audit_expiry_at.isoformat())

    if decision_source != 'existing':
        _track_exposure_event(enrollment.user, enrollment.course_id, variant, expiry_days, decision_source)
