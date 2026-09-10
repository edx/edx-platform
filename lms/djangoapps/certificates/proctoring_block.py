"""Certificate proctoring blocking.

This module intentionally keeps certificate code free of vendor-specific logic.

It exposes a single helper that answers:
"Is this learner blocked from viewing/downloading their certificate due to proctoring review state?"

The frontend remains proctoring-agnostic and relies on LMS-computed flags.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


# Attempt statuses that represent a non-final proctoring state where a review decision
# is still pending.
#
# NOTE: We intentionally keep this list small and aligned with the current requirement
# ("review-required" states). Expand only if the business rule expands.
_BLOCKING_ATTEMPT_STATUSES = {
    'submitted',
    'second_review_required',
}


def is_certificate_view_blocked_due_to_proctoring(user, course_key) -> bool:
    """Return True if certificate view/download should be blocked for `user` in `course_key`.

    Default is **False** for safety/backward compatibility.

    Args:
        user: Django user.
        course_key: CourseKey for the course run.

    Returns:
        bool
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False

    try:
        # Imported lazily to avoid hard dependencies and circular imports in some contexts.
        from edx_proctoring.api import get_all_exams_for_course, get_attempt_status_summary
        from edx_proctoring.exceptions import ProctoredExamNotFoundException
    except Exception:  # pragma: no cover
        # If edx-proctoring isn't installed/enabled in a deployment, default to not blocked.
        return False

    try:
        exams = get_all_exams_for_course(str(course_key)) or []
    except Exception:
        log.exception('Unable to retrieve proctored exams for course_key=%s', course_key)
        return False

    for exam in exams:
        # Only consider active, real proctored exams (exclude practice exams).
        if not (exam.get('is_proctored') and exam.get('is_active') and not exam.get('is_practice_exam')):
            continue

        content_id = exam.get('content_id')
        if not content_id:
            continue

        try:
            summary = get_attempt_status_summary(user.id, str(course_key), content_id) or {}
        except ProctoredExamNotFoundException:
            # No proctored exam attempt exists for this user/content.
            continue
        except Exception:
            log.exception(
                'Unable to retrieve proctoring attempt status summary. user_id=%s course_key=%s content_id=%s',
                user.id, course_key, content_id,
            )
            continue

        attempt_status = summary.get('status')
        if attempt_status in _BLOCKING_ATTEMPT_STATUSES:
            return True

    return False