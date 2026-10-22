"""Certificate access policy for proctored exams.

The proctoring provider owns exam status, but the LMS owns the certificate access
decision.  Keeping the policy here gives all certificate consumers the same
behavior without coupling certificate code to a provider implementation.
"""

import logging

from edx_django_utils.monitoring.utils import increment
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.certificates.config import (
    CERTIFICATE_PROCTORING_REVIEW_BLOCK,
    CERTIFICATE_PROCTORING_REVIEW_BLOCK_EXAMS,
    get_certificate_proctoring_review_block_effective_at,
)
from lms.djangoapps.certificates.exams_client import get_exam_proctoring_statuses
from openedx.core.djangoapps.content.course_overviews.api import get_course_overview_or_none
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
})

ALLOWED_ATTEMPT_STATUSES = frozenset({
    'verified',
    'rejected',
    'timed_out',
    'expired',
})

REVIEW_PENDING_STATUSES = frozenset({'submitted', 'second_review_required'})

# Courses on an LTI proctoring provider (for example Proctorio) keep their attempts in edx-exams.
# The statuses below classify those attempts.  ``rejected`` blocks here, unlike the legacy
# edx-proctoring table above, because a rejected attempt must keep the certificate unavailable.
# ``timed_out`` also blocks: it is a transitional state just before ``submitted``, so allowing it
# could release a certificate before review.
EXAMS_PROVIDER = 'lti_external'

# Only exams of this type can hold a certificate.  edx-exams already omits practice and
# onboarding exams; the LMS ignores them as well so an unfinished practice exam never blocks.
EXAMS_PROCTORED_EXAM_TYPE = 'proctored'

EXAMS_BLOCKING_ATTEMPT_STATUSES = frozenset({
    'created',
    'download_software_clicked',
    'ready_to_start',
    'started',
    'ready_to_submit',
    'submitted',
    'second_review_required',
    'timed_out',
    'error',
})

EXAMS_ALLOWED_ATTEMPT_STATUSES = frozenset({
    'verified',
    'expired',
})

# Legacy edx-proctoring records left behind on a course that moved to an LTI provider.  These
# statuses mean the learner never actually started the old exam, so they must not block.
LEGACY_NEVER_STARTED_STATUSES = frozenset({
    'created',
    'download_software_clicked',
    'ready_to_start',
})


def _cache_key_part(value):
    """Use stable learner IDs while preserving the course-key string."""
    return str(getattr(value, 'id', value))


def _result(blocked=False, reason=None, blocking_statuses=None):
    """Build a stable result for API and UI consumers."""
    if blocked:
        increment('certificates.proctoring_block.blocked')
    return {
        'blocked': blocked,
        'reason': reason,
        'blocking_statuses': blocking_statuses or [],
    }


def _lookup_error_result(blocking_statuses=None):
    """Record a failed lookup and return the blocked fallback result."""
    increment('certificates.proctoring_block.lookup_error')
    return _result(True, 'proctoring_status_unavailable', blocking_statuses)


def _reason_for_status(status):
    """Return the learner-facing reason category for a blocking status."""
    if status in REVIEW_PENDING_STATUSES:
        return 'proctoring_review_pending'
    if status == 'error':
        return 'proctoring_error'
    return 'proctored_exam_incomplete'


def _course_uses_exams_service(course_key):
    """Return whether certificate access should also consult edx-exams for this course."""
    if not CERTIFICATE_PROCTORING_REVIEW_BLOCK_EXAMS.is_enabled():
        return False

    overview = get_course_overview_or_none(CourseKey.from_string(str(course_key)))
    if overview is None:
        # Without an overview the provider is unknown, so the legacy path alone could silently
        # ignore edx-exams attempts.  Report it so the caller fails closed.
        raise ValueError('Course overview not found')

    return overview.proctoring_provider == EXAMS_PROVIDER


def _safe_course_uses_exams_service(user, course_key):
    """Return whether edx-exams applies to the course, or None when that cannot be determined."""
    try:
        return _course_uses_exams_service(course_key)
    except Exception:  # pylint: disable=broad-exception-caught
        # Fail closed: falling back to the legacy path would ignore edx-exams attempts.
        log.exception(
            'Unable to load course overview while checking certificate access. '
            'user_id=%s course_key=%s', user.id, course_key
        )
        return None


def _get_exams_service_result(user, course_key):
    """Return the access decision from the learner's edx-exams proctoring statuses."""
    try:
        exams = get_exam_proctoring_statuses(user, course_key)
    except Exception:  # pylint: disable=broad-exception-caught
        # Fail closed on any failure (including ExamsServiceError and JWT misconfiguration)
        # rather than surfacing a 500 from the certificate access check.
        log.exception(
            'Unable to retrieve edx-exams proctoring status while checking certificate access. '
            'user_id=%s course_key=%s', user.id, course_key
        )
        return _lookup_error_result()

    for exam in exams:
        if 'exam_type' not in exam or 'status' not in exam:
            # A record without these fields must not be read as "no attempt" and allowed.
            log.error(
                'Malformed edx-exams proctoring status while checking certificate access. '
                'user_id=%s course_key=%s content_id=%s', user.id, course_key, exam.get('content_id')
            )
            return _lookup_error_result()

        if exam['exam_type'] != EXAMS_PROCTORED_EXAM_TYPE:
            continue

        status = exam['status']

        # No attempt is not a review-required state, matching the legacy behavior.
        if status is None:
            continue

        if status == 'rejected':
            return _result(True, 'proctoring_rejected', [status])

        if status in EXAMS_BLOCKING_ATTEMPT_STATUSES:
            return _result(True, _reason_for_status(status), [status])

        if status in EXAMS_ALLOWED_ATTEMPT_STATUSES:
            continue

        # A status nobody has classified must not silently bypass the integrity check.
        log.error(
            'Unclassified edx-exams proctoring status while checking certificate access. '
            'user_id=%s course_key=%s content_id=%s status=%s',
            user.id, course_key, exam.get('content_id'), status
        )
        return _lookup_error_result([status])

    return _result()


def _certificate_was_downloadable_at_policy_start(certificate, effective_at):
    """Return whether a certificate was downloadable before the policy started.

    ``created_date`` identifies when the certificate row was first created, not
    necessarily when it became downloadable.  Certificate rows are reused, so
    consult their status history before treating an older row as legacy.
    """
    if certificate is None or effective_at is None:
        return False

    created_at = getattr(certificate, 'created_date', None)
    if created_at is None:
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=effective_at.tzinfo)
    if created_at >= effective_at:
        return False

    from lms.djangoapps.certificates.data import CertificateStatuses

    history = certificate.history.all()
    latest_before_cutoff = history.filter(
        history_date__lt=effective_at,
    ).order_by('-history_date').first()
    if (
        latest_before_cutoff is None
        or latest_before_cutoff.status != CertificateStatuses.downloadable
    ):
        return False

    return not history.filter(
        history_date__gte=effective_at,
    ).exclude(status=CertificateStatuses.downloadable).exists()


def _legacy_exam_status(user, course_key, exam, get_attempt_status_summary):
    """Return ``(status, error_result)`` for one legacy proctored exam.

    ``status`` is None when the learner has no attempt.  ``error_result`` is a blocked
    result when the status could not be determined, otherwise None.
    """
    content_id = exam.get('content_id')
    if not content_id:
        log.error(
            'Proctoring returned an active proctored exam without content_id. '
            'user_id=%s course_key=%s', user.id, course_key
        )
        return None, _lookup_error_result()

    try:
        summary = get_attempt_status_summary(user.id, str(course_key), content_id)
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception(
            'Unable to retrieve proctoring attempt status summary. '
            'user_id=%s course_key=%s content_id=%s', user.id, course_key, content_id
        )
        return None, _lookup_error_result()

    if summary is not None:
        status = summary.get('status')
    else:
        # edx-proctoring returns None both when the learner is not eligible
        # to take the exam and when the exam lookup fails.  Revalidate the
        # exam before treating a missing attempt as non-blocking so a
        # provider lookup failure remains fail-closed.  If an attempt
        # exists, apply its status directly so a permission change cannot
        # hide a review-pending attempt.
        try:
            from edx_proctoring.api import get_current_exam_attempt, get_exam_by_content_id

            get_exam_by_content_id(str(course_key), content_id)
            exam_id = exam.get('id')
            if exam_id is None:
                raise ValueError('Proctoring exam is missing id')
            attempt = get_current_exam_attempt(exam_id, user.id)
        except Exception:  # pylint: disable=broad-exception-caught
            log.exception(
                'Unable to resolve a missing proctoring status summary. '
                'user_id=%s course_key=%s content_id=%s', user.id, course_key, content_id
            )
            return None, _lookup_error_result()

        if not attempt:
            return None, None

        status = attempt.get('status')

    if not status:
        log.error(
            'Proctoring returned no status while checking certificate access. '
            'user_id=%s course_key=%s content_id=%s', user.id, course_key, content_id
        )
        return None, _lookup_error_result()

    return status, None


def _classify_legacy_status(user, course_key, exam, status, use_exams_service):
    """Return a blocked result for a legacy attempt status, or None when it does not block."""
    # pylint: disable=import-outside-toplevel
    from edx_proctoring.statuses import ProctoredExamStudentAttemptStatus

    # ``eligible`` is the edx-proctoring representation for no attempt.
    # A missing attempt is not a review-required state and must not block a
    # learner who otherwise meets the normal certificate requirements.
    # ``expired`` is returned separately once the course-end date passes.
    if status == ProctoredExamStudentAttemptStatus.eligible:
        return None

    # On a course that moved to an LTI provider, an old record whose exam never started is
    # not a reason to hold the certificate.  Real legacy attempts are still respected below.
    if use_exams_service and status in LEGACY_NEVER_STARTED_STATUSES:
        return None

    if status in BLOCKING_ATTEMPT_STATUSES:
        return _result(True, _reason_for_status(status), [status])

    if status in ALLOWED_ATTEMPT_STATUSES:
        return None

    # Known or newly introduced statuses must not silently bypass a
    # certificate-integrity check.  Treat them as unavailable until the
    # policy is explicitly classified.
    log.error(
        'Unclassified proctoring status while checking certificate access. '
        'user_id=%s course_key=%s content_id=%s status=%s',
        user.id, course_key, exam.get('content_id'), status
    )
    return _lookup_error_result([status])


def _get_legacy_proctoring_result(user, course_key, use_exams_service):
    """Return a blocked result from legacy edx-proctoring attempts, or None when none block."""
    try:
        from edx_proctoring.api import get_all_exams_for_course, get_attempt_status_summary
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception(
            'Unable to import edx-proctoring while checking certificate access. '
            'user_id=%s course_key=%s', user.id, course_key
        )
        return _lookup_error_result()

    try:
        exams = get_all_exams_for_course(str(course_key), active_only=True) or []
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception(
            'Unable to retrieve proctored exams while checking certificate access. '
            'user_id=%s course_key=%s', user.id, course_key
        )
        return _lookup_error_result()

    for exam in exams:
        if not isinstance(exam, dict):
            log.error(
                'Proctoring returned a malformed exam while checking certificate access. '
                'user_id=%s course_key=%s', user.id, course_key
            )
            return _lookup_error_result()

        if not (
            exam.get('is_proctored')
            and exam.get('is_active')
            and not exam.get('is_practice_exam')
        ):
            continue

        status, error_result = _legacy_exam_status(user, course_key, exam, get_attempt_status_summary)
        if error_result:
            return error_result
        if status is None:
            continue

        blocked_result = _classify_legacy_status(user, course_key, exam, status, use_exams_service)
        if blocked_result:
            return blocked_result

    return None


@request_cached(
    namespace='certificates.proctoring_block',
    arg_map_function=_cache_key_part,
)
def get_certificate_proctoring_status(user, course_key, certificate=None):
    """Return whether certificate access is blocked for a learner/course.

    The result is calculated from the current status on every request.  The
    platform request cache prevents duplicate checks within that request.  No
    cross-request cache is used so a provider callback automatically restores
    access on the next request.

    Certificates downloadable before the configured policy effective time
    retain the access they had when they were awarded.  This prevents a newly
    enabled access-time policy from retroactively blocking historical
    certificates.

    A status lookup failure is treated conservatively as blocked when the
    feature is enabled.  The failure is logged and exposed through the
    ``proctoring_status_unavailable`` reason instead of silently granting access.
    """
    if (
        not CERTIFICATE_PROCTORING_REVIEW_BLOCK.is_enabled()
        or not user
        or not getattr(user, 'is_authenticated', False)
        or _certificate_was_downloadable_at_policy_start(
            certificate, get_certificate_proctoring_review_block_effective_at()
        )
    ):
        return _result()

    use_exams_service = _safe_course_uses_exams_service(user, course_key)
    if use_exams_service is None:
        return _lookup_error_result()

    legacy_result = _get_legacy_proctoring_result(user, course_key, use_exams_service)
    if legacy_result:
        return legacy_result

    if use_exams_service:
        return _get_exams_service_result(user, course_key)

    return _result()
