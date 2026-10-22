"""Read-only client for the edx-exams proctoring status used by certificate access checks.

The edx-exams service owns attempt state for courses that use an LTI proctoring provider
(for example Proctorio).  The LMS only reads it; the certificate access decision stays in
``proctoring_block.py``.
"""

import logging
from urllib.parse import quote

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from edx_rest_api_client.auth import SuppliedJwtAuth

from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user

log = logging.getLogger(__name__)
User = get_user_model()


class ExamsServiceError(Exception):
    """Raised when edx-exams status cannot be retrieved or understood."""


def _get_exams_api_client():
    """Return a requests session authenticated as the edx-exams service user."""
    service_user = User.objects.get(username=settings.EXAMS_SERVICE_USERNAME)
    client = requests.Session()
    client.auth = SuppliedJwtAuth(create_jwt_for_user(service_user))
    return client


REQUIRED_EXAM_FIELDS = ('exam_id', 'content_id', 'exam_type', 'attempt_id', 'status')


def _is_well_formed_exam(exam):
    """Return whether an exam record has every field and a consistent attempt.

    A learner with no attempt has both ``attempt_id`` and ``status`` set to None.  A record that
    is missing a field, or that has only one of the two, is malformed rather than "no attempt".
    """
    if any(field not in exam for field in REQUIRED_EXAM_FIELDS):
        return False

    status = exam['status']
    if not isinstance(exam['exam_type'], str) or not (status is None or isinstance(status, str)):
        return False

    return (status is None) == (exam['attempt_id'] is None)


def get_exam_proctoring_statuses(user, course_key):
    """Return the learner's latest proctoring status for every proctored exam in the course.

    Returns a list of dicts with ``exam_id``, ``content_id``, ``exam_name``, ``exam_type``,
    ``attempt_id`` and ``status``.  ``attempt_id`` and ``status`` are both None when the learner
    has no attempt.

    Raises:
        ExamsServiceError: the service could not be reached, rejected the request, or returned
            a malformed response.  Callers must not treat this as "no exams".
    """
    encoded_course_key = quote(str(course_key), safe='')
    url = f'{settings.EXAMS_SERVICE_URL}/certificate_proctoring/course_id/{encoded_course_key}/status'

    try:
        with _get_exams_api_client() as client:
            response = client.get(
                url,
                params={'lms_user_id': user.id},
                timeout=settings.EXAMS_SERVICE_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
    except (requests.RequestException, ValueError, User.DoesNotExist) as exc:
        raise ExamsServiceError(f'Unable to read proctoring status from edx-exams: {exc!r}') from exc

    exams = payload.get('exams') if isinstance(payload, dict) else None
    if not isinstance(exams, list) or not all(isinstance(exam, dict) and _is_well_formed_exam(exam) for exam in exams):
        raise ExamsServiceError('edx-exams returned a malformed proctoring status response')

    return exams
