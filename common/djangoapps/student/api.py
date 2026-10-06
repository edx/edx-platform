# pylint: disable=unused-import
"""
Python APIs exposed by the student app to other in-process apps.
"""


from typing import TYPE_CHECKING
import csv
import io
import logging

from django.contrib.auth import get_user_model
from django.conf import settings
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.models_api import create_manual_enrollment_audit as _create_manual_enrollment_audit
from common.djangoapps.student.models_api import get_course_access_role
from common.djangoapps.student.models_api import get_course_enrollment as _get_course_enrollment
from common.djangoapps.student.models_api import (
    ENROLLED_TO_ENROLLED as _ENROLLED_TO_ENROLLED,
    ENROLLED_TO_UNENROLLED as _ENROLLED_TO_UNENROLLED,
    UNENROLLED_TO_ENROLLED as _UNENROLLED_TO_ENROLLED,
    UNENROLLED_TO_UNENROLLED as _UNENROLLED_TO_UNENROLLED,
    UNENROLLED_TO_ALLOWEDTOENROLL as _UNENROLLED_TO_ALLOWEDTOENROLL,
    ALLOWEDTOENROLL_TO_ENROLLED as _ALLOWEDTOENROLL_TO_ENROLLED,
    ALLOWEDTOENROLL_TO_UNENROLLED as _ALLOWEDTOENROLL_TO_UNENROLLED,
    DEFAULT_TRANSITION_STATE as _DEFAULT_TRANSITION_STATE,
)
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseStaffRole,
    GlobalStaff,
    REGISTERED_ACCESS_ROLES as _REGISTERED_ACCESS_ROLES,
)
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

if TYPE_CHECKING:
    from django.contrib.auth.models import AnonymousUser, User  # pylint: disable=imported-auth-user
    from django.db.models.query import QuerySet


# This is done so that if these strings change within the app, we can keep exported constants the same
ENROLLED_TO_ENROLLED = _ENROLLED_TO_ENROLLED
ENROLLED_TO_UNENROLLED = _ENROLLED_TO_UNENROLLED
UNENROLLED_TO_ENROLLED = _UNENROLLED_TO_ENROLLED
UNENROLLED_TO_UNENROLLED = _UNENROLLED_TO_UNENROLLED
UNENROLLED_TO_ALLOWEDTOENROLL = _UNENROLLED_TO_ALLOWEDTOENROLL
ALLOWEDTOENROLL_TO_ENROLLED = _ALLOWEDTOENROLL_TO_ENROLLED
ALLOWEDTOENROLL_TO_UNENROLLED = _ALLOWEDTOENROLL_TO_UNENROLLED
DEFAULT_TRANSITION_STATE = _DEFAULT_TRANSITION_STATE

TRANSITION_STATES = (
    ENROLLED_TO_ENROLLED,
    ENROLLED_TO_UNENROLLED,
    UNENROLLED_TO_ENROLLED,
    UNENROLLED_TO_UNENROLLED,
    UNENROLLED_TO_ALLOWEDTOENROLL,
    ALLOWEDTOENROLL_TO_ENROLLED,
    ALLOWEDTOENROLL_TO_UNENROLLED,
    DEFAULT_TRANSITION_STATE,
)

COURSE_DASHBOARD_PLUGIN_VIEW_NAME = "course_dashboard"

log = logging.getLogger()


def create_manual_enrollment_audit(
    enrolled_by,
    user_email,
    transition_state,
    reason,
    course_run_key=None,
):
    """
    Creates an audit item for a manual enrollment.
    Parameters:
        enrolled_by: <auth.User> of the person that is manually enrolling
        user_email: <str> email of the user being enrolled
        transition_state: <str> state of enrollment transition state from _TRANSITIONS_STATES
        reason: <str> Reason why user was manually enrolled
        course_run_key: <str> Used to link the audit enrollment to the actual enrollment

    Note: We purposefully *exclude* passing items like CourseEnrollment objects to prevent callers from needed to
    know about model level code.
    """
    if transition_state not in TRANSITION_STATES:
        raise ValueError(f"State `{transition_state}` not in allow states: `{TRANSITION_STATES}`")

    User = get_user_model()
    try:
        enrolled_user = User.objects.get(email=user_email)
    except User.DoesNotExist:
        enrolled_user = None

    if enrolled_user and course_run_key:
        enrollment = _get_course_enrollment(enrolled_user, course_run_key)
    else:
        enrollment = None

    _create_manual_enrollment_audit(enrolled_by, user_email, transition_state, reason, enrollment)


def get_access_role_by_role_name(role_name):
    """
    Get the concrete child class of the AccessRole abstract class associated with the string role_name
    by looking in REGISTERED_ACCESS_ROLES. If there is no class associated with this name, return None.

    Note that this will only return classes that are registered in _REGISTERED_ACCESS_ROLES.

    Arguments:
        role_name: the name of the role
    """
    return _REGISTERED_ACCESS_ROLES.get(role_name, None)


def is_user_enrolled_in_course(student, course_key):
    """
    Determines if a learner is enrolled in a given course-run.
    """
    log.info(f"Checking if {student.id} is enrolled in course {course_key}")
    return CourseEnrollment.is_enrolled(student, course_key)


def is_user_staff_or_instructor_in_course(user, course_key):
    """
    Determines if a user is an Instructor or part of the given course's course staff.

    Also returns true for GlobalStaff.
    """
    if not isinstance(course_key, CourseKey):
        course_key = CourseKey.from_string(course_key)

    return (
        GlobalStaff().has_user(user)
        or CourseStaffRole(course_key).has_user(user)
        or CourseInstructorRole(course_key).has_user(user)
    )


def get_course_enrollments(
    user: "AnonymousUser | User",
    is_filtered: bool = False,
    course_ids: list[str | None] | None = None,
) -> "QuerySet[CourseEnrollment]":
    """
    Return enrollments for a user, potentially filtered by course_id.

    Because an empty `course_ids` value is a meaningful filter, the easiest way to verify
    that the list should be filtered intentionally is to specify `is_filtered`.

    Arguments:

    * is_filtered (bool): whether or not the list is filtered
    * course_ids (list): a list of course IDs to filter by.
    """
    course_enrollments = CourseEnrollment.enrollments_for_user(user).select_related("course")

    if is_filtered:
        course_enrollments = course_enrollments.filter(course_id__in=course_ids)

    return course_enrollments


# Header cells (case-insensitive, whitespace-trimmed) that mark an optional first row as a header.
BULK_UNENROLL_CSV_HEADERS = frozenset({"course_id", "course id"})


class BulkUnenrollCsvTooManyRows(Exception):
    """
    Raised as soon as a bulk-unenroll CSV passes the caller's row limit, so an
    oversized file is never parsed in full. Carries the limit so it can be reported.
    """

    def __init__(self, max_rows):
        self.max_rows = max_rows
        super().__init__(f"CSV exceeds the maximum of {max_rows} rows.")


class BulkUnenrollCsvUnreadable(Exception):
    """
    Raised when the upload cannot be read as CSV text at all.

    Distinct from the per-row errors this parser collects: a file that is not UTF-8
    (a ``.xls`` renamed to ``.csv``) or is malformed CSV has no rows to report
    against. Callers turn this into a 400 rather than a 500.
    """


def _iter_csv_rows(text):
    """
    Yield CSV rows, reporting a malformed file as ``BulkUnenrollCsvUnreadable``.

    ``csv.reader`` raises ``csv.Error`` lazily, from the middle of iteration, so the
    guard has to wrap the stepping rather than the reader's construction.
    """
    reader = csv.reader(io.StringIO(text))
    while True:
        try:
            yield next(reader)
        except StopIteration:
            return
        except csv.Error as exc:
            raise BulkUnenrollCsvUnreadable(f"File is not readable as CSV: {exc}") from exc


def parse_bulk_unenroll_csv(file_obj, max_rows=None):
    """
    Parse a bulk-unenroll CSV into course keys.

    UTF-8 (BOM optional), exactly **one** non-empty cell per row: a course id. An
    optional ``course_id`` / ``course id`` header and blank rows are skipped, and
    duplicates are collapsed to the first occurrence without being reported. A row
    with more than one cell is an error — deliberately narrower than the
    ``bulk_unenroll`` management command's ``username,course_id``, whose username
    column the whole-course worker would silently ignore while unenrolling everyone.

    Arguments:
        file_obj: a file-like object opened in binary or text mode.
        max_rows: optional cap on *data* rows (every non-blank, non-header row,
            including duplicates and invalid ones). ``None`` means no limit.

    Raises:
        BulkUnenrollCsvTooManyRows: if ``max_rows`` is exceeded.
        BulkUnenrollCsvUnreadable: if the bytes are not UTF-8 or not parseable CSV.

    Returns:
        (course_keys, errors) — course keys de-duplicated in input order, and
        ``{"row": int, "value": str, "error": str}`` dicts whose 1-based row number
        counts every physical row, so it matches what a spreadsheet shows.
    """
    raw = file_obj.read()
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise BulkUnenrollCsvUnreadable(
                "File is not valid UTF-8 text. Re-save it as a UTF-8 CSV and try again."
            ) from exc
    else:
        # Strip a leading BOM if the caller handed us already-decoded text.
        # Spelled as an escape: the literal character is invisible in a diff.
        text = raw.removeprefix("\ufeff")

    course_keys = []
    errors = []
    seen = set()
    data_rows = 0

    for row_number, row in enumerate(_iter_csv_rows(text), start=1):
        cells = [cell.strip() for cell in row]
        non_empty = [cell for cell in cells if cell]

        if not non_empty:
            # Blank row (possibly all-whitespace or trailing newline) — skip.
            continue

        # Skip a single-cell header row if it names the column.
        if row_number == 1 and len(non_empty) == 1 and non_empty[0].lower() in BULK_UNENROLL_CSV_HEADERS:
            continue

        # Count every data row, not just the ones that survive to `course_keys`:
        # duplicates and invalid rows still cost time and response size.
        data_rows += 1
        if max_rows is not None and data_rows > max_rows:
            raise BulkUnenrollCsvTooManyRows(max_rows)

        raw_value = ",".join(cells).strip(",")

        if len(non_empty) > 1:
            errors.append({
                "row": row_number,
                "value": raw_value,
                "error": "Expected a single course_id column",
            })
            continue

        value = non_empty[0]
        try:
            course_key = CourseKey.from_string(value)
        except InvalidKeyError:
            errors.append({
                "row": row_number,
                "value": value,
                "error": "Invalid course id",
            })
            continue

        if course_key in seen:
            # Duplicate — collapse silently so a course is never double-queued.
            continue
        seen.add(course_key)
        course_keys.append(course_key)

    return course_keys, errors
