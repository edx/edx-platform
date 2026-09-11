"""Certificate access policy for proctored exams.

The proctoring provider owns exam status, but the LMS owns the certificate access
decision.  Keeping the policy here gives all certificate consumers the same
behavior without coupling certificate code to a provider implementation.
"""

import logging

from edx_django_utils.monitoring.utils import increment

from lms.djangoapps.certificates.config import CERTIFICATE_PROCTORING_REVIEW_BLOCK
from openedx.core.lib.cache_utils import request_cached

log = logging.getLogger(__name__)


# These are the statuses from the approved certificate-behavior matrix.  The
# list deliberately contains provider-neutral status values exposed by
# edx-proctoring rather than provider-specific values.
BLOCKING_ATTEMPT_STATUSES = frozenset({
    'created',
    'download_software_clicked',
    'ready_to_start',
    'started',
    'ready_to_submit',
    'submitted',
    'second_review_required',
    'error',
    'not_attempted',
})

ALLOWED_ATTEMPT_STATUSES = frozenset({
    'verified',
    'rejected',
    'timed_out',
    'expired',
})

REVIEW_PENDING_STATUSES = frozenset({'submitted', 'second_review_required'})


def _cache_key_part(value):
    """Use stable learner IDs while preserving the course-key string."""
    return str(getattr(value, 'id', value))


def _result(blocked=False, reason=None, blocking_statuses=None):
    """Build a stable result for API and UI consumers."""
    if blocked:
        increment('certificates.proctoring_block.blocked')
        if reason == 'proctoring_status_unavailable':
            increment('certificates.proctoring_block.lookup_error')
    return {
        'blocked': blocked,
        'reason': reason,
        'blocking_statuses': blocking_statuses or [],
    }


def _reason_for_status(status):
    """Return the learner-facing reason category for a blocking status."""
    if status in REVIEW_PENDING_STATUSES:
        return 'proctoring_review_pending'
    if status == 'error':
        return 'proctoring_error'
    if status == 'not_attempted':
        return 'proctored_exam_not_attempted'
    return 'proctored_exam_incomplete'


@request_cached(
    namespace='certificates.proctoring_block',
    arg_map_function=_cache_key_part,
)
def get_certificate_proctoring_status(user, course_key):
    """Return whether certificate access is blocked for a learner/course.

    The result is calculated from the current status on every request.  The
    platform request cache prevents duplicate checks within that request.  No
    cross-request cache is used so a provider callback automatically restores
    access on the next request.

    A status lookup failure is treated conservatively as blocked when the
    feature is enabled.  The failure is logged and exposed through the
    ``proctoring_status_unavailable`` reason instead of silently granting access.
    """
    if not CERTIFICATE_PROCTORING_REVIEW_BLOCK.is_enabled():
        return _result()

    if not user or not getattr(user, 'is_authenticated', False):
        return _result()

    try:
        from edx_proctoring.api import get_all_exams_for_course, get_attempt_status_summary
        from edx_proctoring.statuses import ProctoredExamStudentAttemptStatus
    except Exception:
        log.exception(
            'Unable to import edx-proctoring while checking certificate access. '
            'user_id=%s course_key=%s', user.id, course_key
        )
        return _result(True, 'proctoring_status_unavailable')

    try:
        exams = get_all_exams_for_course(str(course_key), active_only=True) or []
    except Exception:
        log.exception(
            'Unable to retrieve proctored exams while checking certificate access. '
            'user_id=%s course_key=%s', user.id, course_key
        )
        return _result(True, 'proctoring_status_unavailable')

    for exam in exams:
        if not isinstance(exam, dict):
            log.error(
                'Proctoring returned a malformed exam while checking certificate access. '
                'user_id=%s course_key=%s', user.id, course_key
            )
            return _result(True, 'proctoring_status_unavailable')

        if not (
            exam.get('is_proctored')
            and exam.get('is_active')
            and not exam.get('is_practice_exam')
        ):
            continue

        content_id = exam.get('content_id')
        if not content_id:
            log.error(
                'Proctoring returned an active proctored exam without content_id. '
                'user_id=%s course_key=%s', user.id, course_key
            )
            return _result(True, 'proctoring_status_unavailable')

        try:
            summary = get_attempt_status_summary(user.id, str(course_key), content_id)
        except Exception:
            log.exception(
                'Unable to retrieve proctoring attempt status summary. '
                'user_id=%s course_key=%s content_id=%s', user.id, course_key, content_id
            )
            return _result(True, 'proctoring_status_unavailable')

        if not summary or not summary.get('status'):
            log.error(
                'Proctoring returned no status while checking certificate access. '
                'user_id=%s course_key=%s content_id=%s', user.id, course_key, content_id
            )
            return _result(True, 'proctoring_status_unavailable')

        status = summary['status']
        # ``eligible`` is the edx-proctoring representation for no attempt.
        # ``expired`` is returned separately once the course-end date passes.
        if status == ProctoredExamStudentAttemptStatus.eligible:
            status = 'not_attempted'

        if status in BLOCKING_ATTEMPT_STATUSES:
            return _result(True, _reason_for_status(status), [status])

        if status in ALLOWED_ATTEMPT_STATUSES:
            continue

        # Known or newly introduced statuses must not silently bypass a
        # certificate-integrity check.  Treat them as unavailable until the
        # policy is explicitly classified.
        log.error(
            'Unclassified proctoring status while checking certificate access. '
            'user_id=%s course_key=%s content_id=%s status=%s',
            user.id, course_key, content_id, status
        )
        return _result(True, 'proctoring_status_unavailable', [status])

    return _result()
