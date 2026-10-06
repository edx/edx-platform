"""
Test Student api.py
"""

import csv
import io

import ddt

from django.test import SimpleTestCase
from opaque_keys.edx.keys import CourseKey
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from common.djangoapps.student.api import (
    BulkUnenrollCsvTooManyRows,
    BulkUnenrollCsvUnreadable,
    is_user_enrolled_in_course,
    is_user_staff_or_instructor_in_course,
    get_course_enrollments,
    parse_bulk_unenroll_csv,
)
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import (
    CourseEnrollmentFactory,
    GlobalStaffFactory,
    InstructorFactory,
    StaffFactory,
    UserFactory,
)


class TestStudentApi(SharedModuleStoreTestCase):
    """
    Tests for functionality in the api.py file of the Student django app.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.course_run_key = self.course.id

    def test_is_user_enrolled_in_course(self):
        """
        Verify the correct value is returned when a learner is actively enrolled in a course-run.
        """
        CourseEnrollmentFactory.create(user_id=self.user.id, course_id=self.course.id)

        result = is_user_enrolled_in_course(self.user, self.course_run_key)
        assert result

    def test_is_user_enrolled_in_course_not_active(self):
        """
        Verify the correct value is returned when a learner is not actively enrolled in a course-run.
        """
        CourseEnrollmentFactory.create(user_id=self.user.id, course_id=self.course.id, is_active=False)

        result = is_user_enrolled_in_course(self.user, self.course_run_key)
        assert not result

    def test_is_user_enrolled_in_course_no_enrollment(self):
        """
        Verify the correct value is returned when a learner is not enrolled in a course-run.
        """
        result = is_user_enrolled_in_course(self.user, self.course_run_key)
        assert not result

    def test_is_user_staff_or_instructor(self):
        """
        Verify the correct value is returned for users with different access levels.
        """
        course_id_string = str(self.course.id)
        global_staff_user = GlobalStaffFactory.create()
        staff_user = StaffFactory.create(course_key=self.course_run_key)
        instructor = InstructorFactory.create(course_key=self.course_run_key)

        different_course = CourseFactory.create()
        instructor_different_course = InstructorFactory.create(course_key=different_course.id)

        assert is_user_staff_or_instructor_in_course(instructor, course_id_string)
        assert is_user_staff_or_instructor_in_course(global_staff_user, self.course_run_key)
        assert is_user_staff_or_instructor_in_course(staff_user, self.course_run_key)
        assert is_user_staff_or_instructor_in_course(instructor, self.course_run_key)
        assert not is_user_staff_or_instructor_in_course(self.user, self.course_run_key)
        assert not is_user_staff_or_instructor_in_course(instructor_different_course, self.course_run_key)

    def test_get_course_enrollments(self):
        """Verify all enrollments can be retrieved"""
        course_2 = CourseFactory.create()
        CourseEnrollmentFactory.create(user_id=self.user.id, course_id=self.course.id)
        CourseEnrollmentFactory.create(user_id=self.user.id, course_id=course_2.id)
        expected = CourseEnrollment.objects.all()

        result = get_course_enrollments(self.user)

        self.assertEqual(list(expected), list(result))

    def test_get_filtered_course_enrollments(self):
        """Verify a filtered subset of enrollments can be retrieved"""
        course_2 = CourseFactory.create()
        CourseEnrollmentFactory.create(user_id=self.user.id, course_id=self.course.id)
        ce_2 = CourseEnrollmentFactory.create(user_id=self.user.id, course_id=course_2.id)
        expected = CourseEnrollment.objects.filter(id=ce_2.id)

        result = get_course_enrollments(self.user, True, course_ids=[course_2.id])

        self.assertEqual(list(expected), list(result))


@ddt.ddt
class TestParseBulkUnenrollCsv(SimpleTestCase):
    """
    Tests for parse_bulk_unenroll_csv (pure parser, no DB access).
    """

    DEMO_2024 = CourseKey.from_string("course-v1:edX+DemoX+2024")
    DEMO_2025 = CourseKey.from_string("course-v1:edX+DemoX+2025")

    @staticmethod
    def _file(content):
        """Build a binary file-like object (mimicking a Django UploadedFile)."""
        if isinstance(content, str):
            content = content.encode("utf-8")
        return io.BytesIO(content)

    @ddt.data(
        # (content, expected keys) — one course id per row, header optional.
        ("course-v1:edX+DemoX+2024\ncourse-v1:edX+DemoX+2025\n", 2),
        ("course_id\ncourse-v1:edX+DemoX+2024\n", 1),                 # header skipped
        ("Course ID\ncourse-v1:edX+DemoX+2024\n", 1),                 # header variant
        ("\ufeffcourse_id\ncourse-v1:edX+DemoX+2024\n", 1),            # Excel's BOM
        ("course-v1:edX+DemoX+2024\ncourse-v1:edX+DemoX+2024\n", 1),  # duplicate collapsed
        ("course-v1:edX+DemoX+2024\n\n   \ncourse-v1:edX+DemoX+2025\n", 2),   # blank rows
        ("course-v1:edX+DemoX+2024,\n", 1),                           # stray trailing comma
        ("", 0),                                                     # empty file
    )
    @ddt.unpack
    def test_valid_files_parse_without_errors(self, content, expected_count):
        keys, errors = parse_bulk_unenroll_csv(self._file(content))
        assert not errors
        assert len(keys) == expected_count
        assert keys == list(dict.fromkeys(keys))    # de-duplicated, input order kept

    def test_already_decoded_text_with_a_bom(self):
        """Callers may hand us text rather than bytes; the BOM still has to go."""
        keys, errors = parse_bulk_unenroll_csv(io.StringIO("\ufeffcourse-v1:edX+DemoX+2024\n"))
        assert not errors
        assert keys == [self.DEMO_2024]

    def test_legacy_username_course_id_rejected(self):
        """
        The management command accepts ``username,course_id``; this parser must not.
        The whole-course worker would ignore the username and unenroll everyone.
        """
        keys, errors = parse_bulk_unenroll_csv(self._file(
            "course-v1:edX+DemoX+2024\nalice,course-v1:edX+DemoX+2025\n"
        ))
        assert keys == [self.DEMO_2024]
        assert errors == [{
            "row": 2,
            "value": "alice,course-v1:edX+DemoX+2025",
            "error": "Expected a single course_id column",
        }]

    def test_invalid_course_id_reports_the_spreadsheet_row_number(self):
        """Row numbers count every physical row, header included, so they line up."""
        keys, errors = parse_bulk_unenroll_csv(self._file(
            "course_id\ncourse-v1:edX+DemoX+2024\nbad-key\n"
        ))
        assert keys == [self.DEMO_2024]
        assert errors == [{"row": 3, "value": "bad-key", "error": "Invalid course id"}]

    def test_a_file_of_nothing_but_bad_ids_yields_errors_and_no_keys(self):
        keys, errors = parse_bulk_unenroll_csv(self._file("foo\nbar\n"))
        assert not keys
        assert [e["row"] for e in errors] == [1, 2]
        assert all(e["error"] == "Invalid course id" for e in errors)

    # --- the row limit guards the *file*, not the de-duplicated course list ---

    @ddt.data(
        "course-v1:edX+DemoX+2024\ncourse-v1:edX+DemoX+2025\ncourse-v1:edX+DemoX+2026\n",
        "course-v1:edX+DemoX+2024\n" * 3,     # duplicates still cost time and size
        "bad-1\nbad-2\nbad-3\n",             # so do invalid rows
    )
    def test_max_rows_counts_every_data_row(self, content):
        with self.assertRaises(BulkUnenrollCsvTooManyRows):
            parse_bulk_unenroll_csv(self._file(content), max_rows=2)

    def test_max_rows_ignores_header_and_blank_rows(self):
        keys, errors = parse_bulk_unenroll_csv(
            self._file("course_id\ncourse-v1:edX+DemoX+2024\n\ncourse-v1:edX+DemoX+2025\n\n"),
            max_rows=2,
        )
        assert not errors
        assert len(keys) == 2

    def test_max_rows_none_is_unlimited(self):
        keys, errors = parse_bulk_unenroll_csv(self._file("bad-1\nbad-2\nbad-3\n"))
        assert not keys
        assert len(errors) == 3

    # Unreadable files have no row to attach an error to, so they are raised
    # rather than collected; the upload view turns them into a 400, not a 500.

    @ddt.data(
        b"course-v1:edX+D\xe9moX+2024\n",             # Latin-1 export: never valid UTF-8
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",        # a real .xls saved as .csv
    )
    def test_non_utf8_bytes_are_rejected_as_unreadable(self, content):
        with self.assertRaises(BulkUnenrollCsvUnreadable):
            parse_bulk_unenroll_csv(self._file(content))

    def test_malformed_csv_is_rejected_as_unreadable(self):
        """
        An unterminated quoted field swallows the rest of the file and trips csv's
        field-size limit. csv.reader raises that mid-iteration, after the first row
        was already handed back, so the guard has to wrap the stepping rather than
        the reader's construction.
        """
        runaway_quote = '"' + "a" * (csv.field_size_limit() + 10)
        with self.assertRaises(BulkUnenrollCsvUnreadable):
            parse_bulk_unenroll_csv(self._file(
                "course-v1:edX+DemoX+2024\n" + runaway_quote
            ))
