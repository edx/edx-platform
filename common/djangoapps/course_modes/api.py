"""
Python APIs exposed by the course_modes app to other in-process apps.
"""

import logging

from edx_django_utils.plugins import pluggable_override

from common.djangoapps.course_modes.models import CourseMode as _CourseMode

log = logging.getLogger(__name__)


def get_paid_modes_for_course(course_run_id):
    """
    Returns a list of non-expired mode slugs for a course run ID that have a set minimum price.

    Params:
        course_run_id (CourseKey): The course run you want to get the paid modes for
    Returns:
        A list of paid modes (strings) that the course has attached to it.
    """
    return _CourseMode.paid_modes_for_course(course_run_id)


def is_eligible_for_certificate(mode_slug, status=None):
    """
    Returns whether or not the given mode_slug is eligible for a
    certificate. Currently all modes other than 'audit' grant a
    certificate. Note that audit enrollments which existed prior
    to December 2015 *were* given certificates, so there will be
    GeneratedCertificate records with mode='audit' which are
    eligible.
    """
    return _CourseMode.is_eligible_for_certificate(mode_slug, status)


def get_extra_offer(request, course_key):
    """
    Return an extra offer to show the learner in a course, or None.

    A plugin provides it through the ``OVERRIDE_COURSE_MODES_GET_EXTRA_OFFER`` override point. The track
    selection and course home metadata APIs return it under the ``extra_offer`` key, and leave the key out
    when it is None. If the plugin raises or returns something other than a dict, this logs it and returns
    None, so those APIs keep working.

    Params:
        request (HttpRequest): The current request. The plugin may need its credentials.
        course_key (CourseKey): The course run.
    Returns:
        A JSON-serializable dict, or None.
    """
    try:
        offer = _get_extra_offer(request, course_key)
    except Exception:  # pylint: disable=broad-except
        log.exception("Could not get the extra offer for course %s", course_key)
        return None
    if offer is not None and not isinstance(offer, dict):
        log.error("The extra offer for course %s is a %s, not a dict", course_key, type(offer).__name__)
        return None
    return offer


@pluggable_override("OVERRIDE_COURSE_MODES_GET_EXTRA_OFFER")
def _get_extra_offer(request, course_key):
    """
    Override point for get_extra_offer. Core has no extra offer of its own.

    A plugin lists the dotted path of its function in the ``OVERRIDE_COURSE_MODES_GET_EXTRA_OFFER`` setting.
    That function receives ``prev_fn`` (the next implementation) first, then ``request`` and ``course_key``.
    """
    return None
