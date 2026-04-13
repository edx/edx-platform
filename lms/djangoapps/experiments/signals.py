"""Signal handlers for the experiments app."""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.signals import ENROLLMENT_TRACK_UPDATED
from lms.djangoapps.utils import OptimizelyClient

from .audit_expiry_urgency import (
    EXPERIMENT_KEY,
    get_persisted_expiry_days,
    get_persisted_variant,
    is_target_course,
    maybe_persist_audit_expiry_urgency_attributes,
)
from .flags import AUDIT_EXPIRY_URGENCY_V1_ENABLED

log = logging.getLogger(__name__)


@receiver(post_save, sender=CourseEnrollment, dispatch_uid='audit_expiry_urgency_v1_persist_expiry')
def persist_audit_expiry_urgency_attributes(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """Persist experiment attributes on enrollment save.

    Notes:
    * Must never block enrollment.
    * Runs on *any* save (not just created=True) because enroll() first creates an
      inactive row and then activates it in a subsequent save.
    """
    if kwargs.get('raw'):
        return

    try:
        maybe_persist_audit_expiry_urgency_attributes(instance)
    except Exception:  # pylint: disable=broad-except
        log.exception(
            'Audit expiry urgency: error while persisting attributes for user_id=%s course_id=%s enrollment_id=%s',
            getattr(instance.user, 'id', None),
            getattr(instance, 'course_id', None),
            getattr(instance, 'id', None),
        )


@receiver(ENROLLMENT_TRACK_UPDATED, dispatch_uid='audit_expiry_urgency_v1_track_conversion')
def track_audit_expiry_urgency_conversion(sender, user, course_key, mode, **kwargs):  # pylint: disable=unused-argument
    """Track verified upgrades for experiment participants."""
    try:
        if mode != CourseMode.VERIFIED:
            return

        if not AUDIT_EXPIRY_URGENCY_V1_ENABLED.is_enabled():
            return

        if not is_target_course(course_key):
            return

        enrollment = CourseEnrollment.get_enrollment(user, course_key)
        if not enrollment:
            return

        variant = get_persisted_variant(enrollment)
        if not variant:
            return

        expiry_days = get_persisted_expiry_days(enrollment)

        optimizely_client = OptimizelyClient.get_optimizely_client()
        if optimizely_client:
            optimizely_client.track(
                'audit_expiry_urgency_upgraded_to_verified',
                str(user.id),
                attributes={
                    'experiment_key': EXPERIMENT_KEY,
                    'variant': variant,
                    'course_id': str(course_key),
                    'expiry_days': expiry_days,
                }
            )
    except Exception:  # pylint: disable=broad-except
        log.exception('Audit expiry urgency: failed to track conversion for user_id=%s', getattr(user, 'id', None))
