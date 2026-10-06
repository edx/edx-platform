"""
Tests for support views.
"""

import csv
import uuid as uuid_lib
from unittest.mock import patch

import ddt
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.models.user import CourseAccessRole
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole, SupportStaffRole
from common.djangoapps.student.tests.factories import (
    TEST_PASSWORD,
    AdminFactory,
    InstructorFactory,
    StaffFactory,
    SuperuserFactory,
    UserFactory
)
from lms.djangoapps.support.models import BulkUnenrollBatch, BulkUnenrollChunk, BulkUnenrollCourseState
from lms.djangoapps.support.tests.test_views import SupportViewTestCase
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory


@ddt.ddt
class CourseTeamManageAPIViewTest(SupportViewTestCase):
    """
    Tests for CourseTeamManagementAPIView.

    Covers:
    - GET: Viewing user's course team roles by authenticated users.
    - PUT: Assigning/revoking user roles by authorized users.
    """

    def setUp(self):
        super().setUp()
        self.staff = AdminFactory()
        self.superuser = SuperuserFactory()
        self.user = UserFactory()
        self.org = "TestOrg"
        self.client = APIClient()
        self.url = reverse("lms.djangoapps.support.rest_api:v1:manage_course_team")

        self.extra_courses = [
            CourseOverviewFactory.create(
                org=self.org, run=f"CS10{i}", display_name=f"Test-Course-{i}"
            )
            for i in range(3)
        ]
        self.instructor_user = InstructorFactory.create(
            course_key=self.extra_courses[0].id
        )
        self.staff_user = StaffFactory.create(course_key=self.extra_courses[1].id)

    # --- GET API TEST CASES ---

    def test_get_api_missing_query_params_returns_400(self):
        """GET API: Returns 400 if no query parameters are provided."""
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 400)

    def test_get_api_with_username_parameter(self):
        """GET API: Can query by username parameter."""
        self.client.login(username=self.staff.username, password=TEST_PASSWORD)
        resp = self.client.get(self.url, {"username": self.user.username})
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data, list)

    def test_get_api_with_user_id_parameter(self):
        """GET API: Can query by user_id parameter."""
        self.client.login(username=self.staff.username, password=TEST_PASSWORD)
        resp = self.client.get(self.url, {"user_id": self.user.id})
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data, list)

    def test_get_api_nonexistent_user_returns_404(self):
        """GET API: Returns 404 for a nonexistent user email."""
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        resp = self.client.get(self.url, {"email": "notfound@example.com"})
        self.assertEqual(resp.status_code, 404)

    def test_get_api_unauthenticated_user_returns_401(self):
        """GET API: Unauthenticated users receive 401 Unauthorized."""
        resp = self.client.get(self.url, {"email": self.user.email})
        self.assertEqual(resp.status_code, 401)

    @ddt.data(
        ("instructor", "instructor"),
        ("staff", "staff"),
    )
    @ddt.unpack
    def test_get_api_admin_can_fetch_course_roles(self, assigned_role, expected_role):
        """GET API: Admin/staff user can view roles for any course for target user."""
        self.client.login(username=self.staff.username, password=TEST_PASSWORD)
        CourseAccessRole.objects.filter(user=self.user, org=self.org).delete()

        if assigned_role == "instructor":
            CourseInstructorRole(self.extra_courses[0].id).add_users(self.user)
        elif assigned_role == "staff":
            CourseStaffRole(self.extra_courses[0].id).add_users(self.user)

        response = self.client.get(self.url, {"email": self.user.email})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list)

        course_found = False
        for course in response.data:
            if course["course_id"] == str(self.extra_courses[0].id):
                with self.subTest(role=assigned_role):
                    self.assertEqual(course["role"], expected_role)
                    # Verify new fields are present
                    self.assertIn("status", course)
                    self.assertIn("org", course)
                    self.assertIn("run", course)
                    self.assertIn("number", course)
                    self.assertIn("course_name", course)
                    self.assertIn("course_url", course)
                course_found = True
        self.assertTrue(course_found, "Expected course not found in response.")

    def test_get_api_instructor_can_only_see_their_courses(self):
        """GET API: Course instructor sees only courses they have access to."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        resp = self.client.get(self.url, {"email": self.user.email})
        self.assertEqual(resp.status_code, 200)

        course_ids = [course["course_id"] for course in resp.data]
        self.assertIn(str(self.extra_courses[0].id), course_ids)
        for i in range(1, 3):
            self.assertNotIn(str(self.extra_courses[i].id), course_ids)

    def test_get_api_user_with_no_access_sees_no_courses(self):
        """GET API: Non-instructor users see no courses in the response."""
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        resp = self.client.get(self.url, {"email": self.user.email})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])

    # --- PUT API TEST CASES ---

    def test_put_api_missing_email_returns_400(self):
        """PUT API: Sending request without email returns 400 with an error message."""
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        data = {"bulk_role_operations": []}
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["email"], "")
        result = resp.data["results"][0]
        self.assertEqual(result["status"], "failed")
        self.assertIn("error", result)
        self.assertIn("email", result["error"])

    def test_put_api_empty_operations_returns_400(self):
        """PUT API: Sending empty bulk_role_operations returns 400 with an error message."""
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        data = {"email": "test@example.com", "bulk_role_operations": []}
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["email"], "test@example.com")
        result = resp.data["results"][0]
        self.assertEqual(result["status"], "failed")
        self.assertIn("error", result)
        self.assertIn("bulk_role_operations", result["error"])

    def test_put_api_non_dict_input_returns_400(self):
        """PUT API: Sending a non-dict input returns 400 with an error message."""
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        resp = self.client.put(self.url, ["invalid"], format="json")
        self.assertEqual(resp.status_code, 400)
        result = resp.data["results"][0]
        self.assertEqual(result["status"], "failed")
        self.assertIn("error", result)
        self.assertIn("JSON object", result["error"])

    def test_put_api_missing_fields_returns_error(self):
        """PUT API: Request with missing required fields returns field-level errors."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        data = {
            "email": self.user.email,
            "bulk_role_operations": [{"course_id": "", "role": "", "action": ""}],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["email"], self.user.email)
        result = resp.data["results"][0]
        self.assertEqual(result["status"], "failed")
        for field in ("course_id", "role", "action"):
            self.assertIn(field, result)

    def test_put_api_invalid_user_email_returns_error(self):
        """PUT API: Invalid user email in request returns an error."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        data = {
            "email": "notfound@example.com",
            "bulk_role_operations": [
                {
                    "course_id": str(self.extra_courses[0].id),
                    "role": "instructor",
                    "action": "assign",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.data["email"], "notfound@example.com")
        result = resp.data["results"][0]
        self.assertEqual(result["status"], "failed")
        self.assertIn("User not found", result["error"])

    def test_put_api_invalid_course_id_returns_error(self):
        """PUT API: Invalid course_id in request returns an error."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": "invalid-course-id",
                    "role": "instructor",
                    "action": "assign",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["email"], self.user.email)
        result = resp.data["results"][0]
        self.assertEqual(result["status"], "failed")
        self.assertIn("Invalid course_id", result["error"])

    def test_put_api_invalid_action_returns_error(self):
        """PUT API: Invalid action value in request returns an error."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": str(self.extra_courses[0].id),
                    "role": "instructor",
                    "action": "invalid_action",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["email"], self.user.email)
        result = resp.data["results"][0]
        self.assertEqual(result["status"], "failed")
        self.assertIn("Invalid action", result["error"])

    def test_put_api_assign_role_enrolls_user(self):
        """PUT API: Assigning a role enrolls user in the course."""
        self.client.login(username=self.staff.username, password=TEST_PASSWORD)
        course = self.extra_courses[2]
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": str(course.id),
                    "role": "instructor",
                    "action": "assign",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["email"], self.user.email)
        self.assertEqual(resp.data["results"][0]["status"], "success")
        self.assertTrue(CourseInstructorRole(course.id).has_user(self.user))
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, course.id))

    def test_put_api_revoke_role_removes_user(self):
        """PUT API: Revoking a role removes user from course team."""
        self.client.login(username=self.staff.username, password=TEST_PASSWORD)
        course = self.extra_courses[1]
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": str(course.id),
                    "role": "staff",
                    "action": "revoke",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["email"], self.user.email)
        self.assertEqual(resp.data["results"][0]["status"], "success")
        self.assertFalse(CourseStaffRole(course.id).has_user(self.user))

    def test_put_api_course_instructor_can_manage_own_courses(self):
        """PUT API: Course instructor can assign roles for courses they manage."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": str(self.extra_courses[0].id),
                    "role": "staff",
                    "action": "assign",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["email"], self.user.email)
        self.assertEqual(resp.data["results"][0]["status"], "success")
        self.assertTrue(CourseStaffRole(self.extra_courses[0].id).has_user(self.user))

    def test_put_api_non_instructor_user_forbidden(self):
        """PUT API: Non-instructor users receive 403 Forbidden when assigning roles."""
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": str(self.extra_courses[0].id),
                    "role": "staff",
                    "action": "assign",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data["email"], self.user.email)
        self.assertEqual(resp.data["results"][0]["status"], "failed")
        self.assertIn("do not have permission", resp.data["results"][0]["error"])

    def test_put_api_org_level_instructor_can_manage_all_org_courses(self):
        """PUT API: Org-level instructors can manage roles for all courses in their org."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        CourseAccessRole.objects.create(
            user=self.instructor_user, role="instructor", org=self.org, course_id=None
        )
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": str(self.extra_courses[1].id),
                    "role": "staff",
                    "action": "assign",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["email"], self.user.email)
        self.assertEqual(resp.data["results"][0]["status"], "success")
        self.assertTrue(CourseStaffRole(self.extra_courses[1].id).has_user(self.user))

    def test_put_api_course_instructor_cannot_manage_other_courses(self):
        """PUT API: Course instructor cannot assign roles for courses they don't manage."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": str(self.extra_courses[1].id),
                    "role": "staff",
                    "action": "assign",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["email"], self.user.email)
        self.assertEqual(resp.data["results"][0]["status"], "failed")
        self.assertIn("do not have instructor access", resp.data["results"][0]["error"])


class BulkUnenrollEndpointTestMixin:
    """
    Shared fixtures and the global-staff gate every bulk-unenroll endpoint must have.

    Each endpoint declares its ``url_name`` and implements ``_request``. The gate is
    asserted per endpoint because each view names its own ``permission_classes``, so
    one that forgets ``IsAdminUser`` — or settles for the support-role gate — fails here.
    """

    #: URL name under ``lms.djangoapps.support.rest_api:v1``.
    url_name = None
    #: Patch the engine hand-off so the endpoint can be tested without running the
    #: workers (tasks run eagerly under test). Sets ``self.mock_dispatch``.
    patch_dispatch = False

    def setUp(self):
        super().setUp()
        self.admin = AdminFactory()          # global staff (is_staff=True)
        self.plain_user = UserFactory()
        self.client = APIClient()
        if self.patch_dispatch:
            patcher = patch("lms.djangoapps.support.rest_api.v1.views._dispatch_bulk_unenroll")
            self.mock_dispatch = patcher.start()
            self.addCleanup(patcher.stop)

    def _login(self, user):
        self.client.login(username=user.username, password=TEST_PASSWORD)

    def _url(self, batch_id=None):
        name = f"lms.djangoapps.support.rest_api:v1:{self.url_name}"
        if batch_id is None:
            return reverse(name)
        return reverse(name, kwargs={"batch_id": str(batch_id)})

    def _request(self, batch_id=None):
        """One call to the endpoint under test; ``None`` means the class's own fixture."""
        raise NotImplementedError

    def test_plain_user_forbidden(self):
        self._login(self.plain_user)
        self.assertEqual(self._request().status_code, 403)

    def test_support_staff_role_forbidden(self):
        """A SupportStaffRole holder is not global staff — this gate is the narrower one."""
        support_user = UserFactory()
        SupportStaffRole().add_users(support_user)
        self._login(support_user)
        self.assertEqual(self._request().status_code, 403)

    def test_anonymous_forbidden(self):
        self.assertIn(self._request().status_code, (401, 403))


class BatchScopedEndpointTestMixin(BulkUnenrollEndpointTestMixin):
    """A bulk-unenroll endpoint addressed by ``<batch_id>``."""

    def test_unknown_batch_returns_404(self):
        self._login(self.admin)
        self.assertEqual(self._request(uuid_lib.uuid4()).status_code, 404)


@ddt.ddt
class BulkUnenrollAPIViewTest(BulkUnenrollEndpointTestMixin, SupportViewTestCase):
    """
    Tests for BulkUnenrollAPIView (POST /api/support/v1/bulk_unenroll/).
    """

    url_name = "bulk_unenroll"

    def setUp(self):
        super().setUp()
        self.url = self._url()

        self.course_a = CourseOverviewFactory.create(org="edX", run="A", display_name="A")
        self.course_b = CourseOverviewFactory.create(org="edX", run="B", display_name="B")
        self._enroll(self.course_a.id, 3)
        self._enroll(self.course_b.id, 2)
        # An inactive enrollment must NOT be counted.
        CourseEnrollment.objects.create(
            user=UserFactory(), course_id=self.course_a.id, is_active=False,
        )

    @staticmethod
    def _enroll(course_id, count):
        for _ in range(count):
            CourseEnrollment.objects.create(user=UserFactory(), course_id=course_id, is_active=True)

    @staticmethod
    def _csv(text):
        return SimpleUploadedFile("courses.csv", text.encode("utf-8"), content_type="text/csv")

    def _request(self, batch_id=None):
        return self.client.post(
            self.url, {"file": self._csv(f"{self.course_a.id}\n")}, format="multipart",
        )

    def test_upload_creates_validated_batch_without_mutating(self):
        self._login(self.admin)
        active_before = CourseEnrollment.objects.filter(is_active=True).count()
        content = f"course_id\n{self.course_a.id}\n{self.course_b.id}\n"
        resp = self.client.post(self.url, {"file": self._csv(content)}, format="multipart")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["state"], "validated")
        self.assertEqual(data["total_courses"], 2)
        self.assertEqual(data["totals"]["active"], 5)          # 3 + 2, inactive excluded
        self.assertEqual(data["errors"], [])
        counts = {c["course_id"]: c["active_count"] for c in data["courses"]}
        self.assertEqual(counts[str(self.course_a.id)], 3)
        self.assertEqual(counts[str(self.course_b.id)], 2)

        batch = BulkUnenrollBatch.objects.get(uuid=data["batch_id"])
        self.assertEqual(batch.requester, self.admin)
        self.assertEqual(batch.courses.count(), 2)
        # The preview stages a batch and nothing else: upload unenrolls nobody.
        self.assertEqual(CourseEnrollment.objects.filter(is_active=True).count(), active_before)

    def test_errors_reported_without_aborting(self):
        self._login(self.admin)
        content = f"{self.course_a.id}\nnot-a-key\nalice,{self.course_b.id}\n"
        resp = self.client.post(self.url, {"file": self._csv(content)}, format="multipart")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_courses"], 1)             # only course_a is valid
        rows = {e["row"]: e["error"] for e in data["errors"]}
        self.assertEqual(rows[2], "Invalid course id")
        self.assertEqual(rows[3], "Expected a single course_id column")

    def test_all_invalid_returns_400_no_batch(self):
        self._login(self.admin)
        resp = self.client.post(self.url, {"file": self._csv("foo\nbar\n")}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(BulkUnenrollBatch.objects.count(), 0)

    def test_missing_file_returns_400(self):
        self._login(self.admin)
        resp = self.client.post(self.url, {}, format="multipart")
        self.assertEqual(resp.status_code, 400)

    @ddt.data("latin1", "runaway_quote")
    def test_unreadable_upload_returns_400_not_500(self, kind):
        """
        Bad input, not a server fault: the operator is told to re-save the file.
        """
        self._login(self.admin)
        if kind == "latin1":
            upload = SimpleUploadedFile(
                "courses.csv", b"course-v1:edX+D\xe9moX+2024\n", content_type="text/csv",
            )
        else:
            upload = self._csv('"' + "a" * (csv.field_size_limit() + 10))

        resp = self.client.post(self.url, {"file": upload}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(BulkUnenrollBatch.objects.count(), 0)

    @override_settings(BULK_UNENROLL_MAX_FILE_BYTES=10)
    def test_file_over_size_limit_returns_413(self):
        self._login(self.admin)
        content = f"course_id\n{self.course_a.id}\n"     # > 10 bytes
        resp = self.client.post(self.url, {"file": self._csv(content)}, format="multipart")
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(BulkUnenrollBatch.objects.count(), 0)

    @ddt.data("valid", "duplicate", "invalid")
    @override_settings(BULK_UNENROLL_MAX_ROWS=2)
    def test_row_limit_counts_every_data_row(self, kind):
        """The limit guards the uploaded file, not the de-duplicated course list."""
        content = {
            "valid": f"{self.course_a.id}\n{self.course_b.id}\ncourse-v1:edX+Third+2099\n",
            "duplicate": f"{self.course_a.id}\n" * 3,          # collapses to 1 key, 3 rows
            "invalid": f"{self.course_a.id}\nnot-a-key\nalso-not-a-key\n",
        }[kind]
        self._login(self.admin)
        resp = self.client.post(self.url, {"file": self._csv(content)}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(BulkUnenrollBatch.objects.count(), 0)

    def test_course_not_found_flagged_not_dropped(self):
        self._login(self.admin)
        missing = "course-v1:edX+Ghost+2099"
        content = f"{self.course_a.id}\n{missing}\n"
        resp = self.client.post(self.url, {"file": self._csv(content)}, format="multipart")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_courses"], 2)             # not dropped
        by_id = {c["course_id"]: c for c in data["courses"]}
        self.assertEqual(by_id[missing]["error"], "Course not found")
        self.assertEqual(by_id[missing]["active_count"], 0)
        self.assertEqual(by_id[str(self.course_a.id)]["error"], "")

    def test_query_count_independent_of_enrollment_count(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._login(self.admin)
        few = CourseOverviewFactory.create(org="edX", run="Few", display_name="Few")
        many = CourseOverviewFactory.create(org="edX", run="Many", display_name="Many")
        self._enroll(few.id, 2)
        self._enroll(many.id, 40)

        def upload(course_id):
            with CaptureQueriesContext(connection) as ctx:
                resp = self.client.post(
                    self.url, {"file": self._csv(f"{course_id}\n")}, format="multipart"
                )
            self.assertEqual(resp.status_code, 200)
            return len(ctx.captured_queries)

        # Warm process-global caches so first-request overhead doesn't skew this.
        upload(self.course_a.id)

        # Same query count at 2 and 40 enrollments: no per-enrollment fan-out.
        self.assertEqual(upload(few.id), upload(many.id))


class BulkUnenrollListAPIViewTest(BulkUnenrollEndpointTestMixin, SupportViewTestCase):
    """
    Tests for BulkUnenrollAPIView's list route (GET /api/support/v1/bulk_unenroll/).
    """

    url_name = "bulk_unenroll"

    def setUp(self):
        super().setUp()
        self.url = self._url()

    def _request(self, batch_id=None):
        return self.client.get(self.url)

    def _batch(self, state, requester=None, reason="cleanup", filename="courses.csv"):
        return BulkUnenrollBatch.objects.create(
            requester=requester or self.admin,
            state=state,
            reason=reason,
            csv_filename=filename,
            total_courses=2,
        )

    def test_lists_batches_newest_first_with_identifying_fields(self):
        """A uuid is unrecognizable; the operator identifies a batch by who/why/when."""
        older = self._batch(BulkUnenrollBatch.State.SUCCEEDED)
        newer = self._batch(
            BulkUnenrollBatch.State.RUNNING, reason="LP-860 cleanup", filename="q3.csv",
        )
        self._login(self.admin)

        resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        rows = resp.json()["results"]
        self.assertEqual([r["batch_id"] for r in rows], [str(newer.uuid), str(older.uuid)])
        self.assertEqual(rows[0]["reason"], "LP-860 cleanup")
        self.assertEqual(rows[0]["csv_filename"], "q3.csv")
        self.assertEqual(rows[0]["requester"], self.admin.username)
        self.assertEqual(rows[0]["state"], "running")
        self.assertEqual(rows[0]["total_courses"], 2)
        self.assertIn("created", rows[0])
        self.assertIn("modified", rows[0])

    def test_state_filter_accepts_several_states(self):
        pending = self._batch(BulkUnenrollBatch.State.PENDING)
        running = self._batch(BulkUnenrollBatch.State.RUNNING)
        self._batch(BulkUnenrollBatch.State.SUCCEEDED)
        self._batch(BulkUnenrollBatch.State.VALIDATED)
        self._login(self.admin)

        resp = self.client.get(self.url, {"state": "pending,running"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            {row["batch_id"] for row in resp.json()["results"]},
            {str(pending.uuid), str(running.uuid)},
        )

    def test_unknown_state_returns_400(self):
        self._login(self.admin)
        resp = self.client.get(self.url, {"state": "running,bogus"})
        self.assertEqual(resp.status_code, 400)

    def test_lists_batches_from_every_requester(self):
        """Recovering a lost batch id is the point; a colleague's run is the same problem."""
        other_admin = AdminFactory()
        mine = self._batch(BulkUnenrollBatch.State.RUNNING)
        theirs = self._batch(BulkUnenrollBatch.State.RUNNING, requester=other_admin)
        self._login(self.admin)

        resp = self.client.get(self.url)

        self.assertEqual(
            {row["batch_id"] for row in resp.json()["results"]},
            {str(mine.uuid), str(theirs.uuid)},
        )

    def test_query_count_is_flat_in_batch_count(self):
        """select_related('requester') — the username must not cost a query per row."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._login(self.admin)
        self._batch(BulkUnenrollBatch.State.RUNNING)
        self.client.get(self.url)  # warm caches

        with CaptureQueriesContext(connection) as one_batch:
            self.client.get(self.url)

        for _ in range(4):
            self._batch(BulkUnenrollBatch.State.RUNNING, requester=AdminFactory())

        with CaptureQueriesContext(connection) as five_batches:
            self.client.get(self.url)

        self.assertEqual(len(one_batch.captured_queries), len(five_batches.captured_queries))


@ddt.ddt
class BulkUnenrollConfirmAPIViewTest(BatchScopedEndpointTestMixin, SupportViewTestCase):
    """
    Tests for BulkUnenrollConfirmAPIView
    (POST /api/support/v1/bulk_unenroll/<batch_id>/confirm/).
    """

    url_name = "bulk_unenroll_confirm"
    patch_dispatch = True

    def setUp(self):
        super().setUp()
        self.course = CourseOverviewFactory.create(org="edX", run="A", display_name="A")

    def _request(self, batch_id=None):
        return self._confirm(self._make_batch().uuid if batch_id is None else batch_id)

    def _make_batch(self, state=BulkUnenrollBatch.State.VALIDATED):
        """Create a persisted batch (as the upload endpoint would) in the given state."""
        batch = BulkUnenrollBatch.objects.create(
            requester=self.admin,
            csv_filename="courses.csv",
            total_courses=1,
            state=state,
        )
        BulkUnenrollCourseState.objects.create(batch=batch, course_id=self.course.id, active_count=3)
        return batch

    def _confirm(self, batch_id, reason="cleanup"):
        """One POST to the confirm endpoint, running the queued on_commit hook."""
        data = {} if reason is None else {"reason": reason}
        # The engine hand-off is a transaction.on_commit hook, and TestCase never
        # commits — without this it would silently never run.
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self._url(batch_id), data, format="json")

    def test_confirm_flips_validated_to_pending(self):
        self._login(self.admin)
        batch = self._make_batch()
        resp = self._confirm(batch.uuid, reason="Partner offboarding LP-860")

        self.assertEqual(resp.status_code, 202)
        data = resp.json()
        self.assertEqual(data["batch_id"], str(batch.uuid))
        self.assertEqual(data["state"], "pending")

        batch.refresh_from_db()
        self.assertEqual(batch.state, "pending")
        self.assertEqual(batch.reason, "Partner offboarding LP-860")

    def test_confirm_hands_off_to_the_engine_without_mutating_in_request(self):
        self._login(self.admin)
        CourseEnrollment.objects.create(user=UserFactory(), course_id=self.course.id, is_active=True)
        active_before = CourseEnrollment.objects.filter(is_active=True).count()
        self._confirm(self._make_batch().uuid)
        self.mock_dispatch.assert_called_once()
        self.assertEqual(CourseEnrollment.objects.filter(is_active=True).count(), active_before)

    @ddt.data(None, "   ")
    def test_missing_or_blank_reason_returns_400(self, reason):
        self._login(self.admin)
        batch = self._make_batch()
        resp = self._confirm(batch.uuid, reason=reason)
        self.assertEqual(resp.status_code, 400)
        batch.refresh_from_db()
        self.assertEqual(batch.state, "validated")   # unchanged

    @ddt.data(
        BulkUnenrollBatch.State.PENDING,
        BulkUnenrollBatch.State.RUNNING,
        BulkUnenrollBatch.State.CANCELLED,
        BulkUnenrollBatch.State.SUCCEEDED,
    )
    def test_non_validated_batch_returns_409(self, state):
        self._login(self.admin)
        batch = self._make_batch(state=state)
        resp = self._confirm(batch.uuid)
        self.assertEqual(resp.status_code, 409)
        batch.refresh_from_db()
        self.assertEqual(batch.state, state)         # not re-dispatched
        self.mock_dispatch.assert_not_called()

    def test_failed_dispatch_restores_the_batch(self):
        """
        The flip commits before the publish, so a batch left 'pending' after a failed
        publish is stranded: no worker holds it and confirm would refuse to re-run.

        The publish runs in an on_commit hook, after the response is built, so this
        can no longer answer 503 — the operator sees 202 and then a batch that is
        'validated' again. The rollback itself is what must hold.
        """
        self._login(self.admin)
        batch = self._make_batch()
        self.mock_dispatch.side_effect = OSError("broker unreachable")

        resp = self._confirm(batch.uuid, reason="Partner offboarding")

        self.assertEqual(resp.status_code, 202)
        batch.refresh_from_db()
        self.assertEqual(batch.state, "validated")   # confirmable again
        self.assertEqual(batch.reason, "")

    def test_concurrent_confirm_does_not_double_dispatch(self):
        """
        The state check has to happen on a locked, freshly-read row, or two
        dispatchers run over the same batch.
        """
        self._login(self.admin)
        batch = self._make_batch()
        stale = BulkUnenrollBatch.objects.get(pk=batch.pk)          # says 'validated'
        BulkUnenrollBatch.objects.filter(pk=batch.pk).update(state=BulkUnenrollBatch.State.PENDING)

        with patch.object(BulkUnenrollBatch.objects, "get", return_value=stale):
            resp = self._confirm(batch.uuid)

        self.assertEqual(resp.status_code, 409)
        self.mock_dispatch.assert_not_called()


class BulkUnenrollStatusAPIViewTest(BatchScopedEndpointTestMixin, SupportViewTestCase):
    """
    Tests for BulkUnenrollStatusAPIView
    (GET /api/support/v1/bulk_unenroll/<batch_id>/).
    """

    url_name = "bulk_unenroll_status"

    def setUp(self):
        super().setUp()
        self.batch = BulkUnenrollBatch.objects.create(
            requester=self.admin,
            csv_filename="courses.csv",
            reason="cleanup",
            total_courses=3,
            state=BulkUnenrollBatch.State.PENDING,
        )
        self.rows = [
            BulkUnenrollCourseState.objects.create(
                batch=self.batch,
                course_id=CourseOverviewFactory.create(org="edX", run=f"R{i}", display_name=f"C{i}").id,
                active_count=count,
                state=state,
            )
            for i, (count, state) in enumerate([
                (10, BulkUnenrollCourseState.State.PENDING),
                (20, BulkUnenrollCourseState.State.FAILED),
                (30, BulkUnenrollCourseState.State.PENDING),
            ])
        ]

    def _request(self, batch_id=None):
        return self.client.get(self._url(batch_id or self.batch.uuid))

    def test_status_returns_summary_and_paginated_courses(self):
        self._login(self.admin)
        resp = self.client.get(self._url(self.batch.uuid))

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["batch_id"], str(self.batch.uuid))
        self.assertEqual(data["state"], "pending")
        self.assertEqual(data["reason"], "cleanup")
        self.assertEqual(data["total_courses"], 3)
        self.assertEqual(data["totals"]["active"], 60)
        self.assertEqual(data["courses"]["count"], 3)
        self.assertEqual(len(data["courses"]["results"]), 3)
        self.assertIn("next", data["courses"])
        self.assertIn("previous", data["courses"])

    def test_state_filter_narrows_courses_but_not_summary(self):
        self._login(self.admin)
        self.rows[0].unenrolled = 5
        self.rows[0].save()
        self.rows[1].unenrolled = 10
        self.rows[1].save()

        resp = self.client.get(self._url(self.batch.uuid), {"state": "failed"})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["courses"]["count"], 1)
        self.assertEqual(data["courses"]["results"][0]["state"], "failed")
        self.assertEqual(data["totals"]["active"], 60)
        self.assertEqual(data["totals"]["unenrolled"], 15)

    def test_state_filter_accepts_a_comma_separated_list(self):
        """
        The UI offers grouped choices ("in progress" == pending,running), so a comma
        list must select the union rather than silently matching nothing.
        """
        self._login(self.admin)
        resp = self.client.get(self._url(self.batch.uuid), {"state": "pending,failed"})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["courses"]["count"], 3)
        self.assertEqual(
            sorted(row["state"] for row in data["courses"]["results"]),
            ["failed", "pending", "pending"],
        )
        self.assertEqual(data["totals"]["active"], 60)

    def test_state_filter_rejects_an_unknown_state(self):
        """A typo must say so rather than silently listing nothing."""
        self._login(self.admin)
        resp = self.client.get(self._url(self.batch.uuid), {"state": "pending,bogus"})

        self.assertEqual(resp.status_code, 400)
        self.assertIn("bogus", str(resp.json()))

    def test_course_rows_expose_progress_counters(self):
        self._login(self.admin)
        row = self.rows[1]
        row.unenrolled = 7
        row.already_inactive = 2
        row.failed_count = 1
        row.chunks_total = 4
        row.chunks_finished = 3
        row.save()

        resp = self.client.get(self._url(self.batch.uuid))
        self.assertEqual(resp.status_code, 200)
        listed = {r["course_id"]: r for r in resp.json()["courses"]["results"]}
        got = listed[str(row.course_id)]

        self.assertEqual(got["unenrolled"], 7)
        self.assertEqual(got["already_inactive"], 2)
        self.assertEqual(got["failed_count"], 1)
        self.assertEqual(got["chunks_total"], 4)
        self.assertEqual(got["chunks_finished"], 3)
        self.assertEqual(got["active_count"], 20)

    def test_totals_aggregate_progress_across_whole_batch(self):
        self._login(self.admin)
        # Zeros, not nulls, before any work — the UI renders these directly and
        # Sum() over an untouched batch would otherwise return None.
        untouched = self.client.get(self._url(self.batch.uuid)).json()["totals"]
        self.assertEqual(
            [untouched["unenrolled"], untouched["already_inactive"], untouched["failed"]],
            [0, 0, 0],
        )

        for row, (unenrolled, inactive, failed) in zip(self.rows, [(5, 1, 0), (10, 0, 2), (0, 0, 0)]):
            row.unenrolled = unenrolled
            row.already_inactive = inactive
            row.failed_count = failed
            row.save()

        data = self.client.get(self._url(self.batch.uuid)).json()

        self.assertEqual(data["totals"]["active"], 60)          # unchanged, still the preview sum
        self.assertEqual(data["totals"]["unenrolled"], 15)
        self.assertEqual(data["totals"]["already_inactive"], 1)
        self.assertEqual(data["totals"]["failed"], 2)
        # Courses finished = those in a terminal state (1 failed, 0 succeeded).
        self.assertEqual(data["totals"]["courses_finished"], 1)

    def test_status_query_count_is_flat_in_course_count(self):
        """Polling cost must not grow with batch size — the summary stays one aggregate."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._login(self.admin)

        def poll():
            with CaptureQueriesContext(connection) as ctx:
                resp = self.client.get(self._url(self.batch.uuid))
                self.assertEqual(resp.status_code, 200)
            return len(ctx)

        # Warm session/waffle/site caches: the first request's middleware lookups
        # would otherwise swamp the signal.
        poll()
        small = poll()
        for i in range(20):
            BulkUnenrollCourseState.objects.create(
                batch=self.batch,
                course_id=CourseOverviewFactory.create(org="edX", run=f"Q{i}", display_name=f"Q{i}").id,
                active_count=1,
            )
        self.assertEqual(poll(), small)


@ddt.ddt
class BulkUnenrollCancelAPIViewTest(BatchScopedEndpointTestMixin, SupportViewTestCase):
    """POST /api/support/v1/bulk_unenroll/<batch_id>/cancel/."""

    url_name = "bulk_unenroll_cancel"

    def _make_batch(self, state):
        return BulkUnenrollBatch.objects.create(
            requester=self.admin, total_courses=0, state=state,
        )

    def _cancel(self, batch_id):
        return self.client.post(self._url(batch_id), {}, format="json")

    def _request(self, batch_id=None):
        if batch_id is None:
            batch_id = self._make_batch(BulkUnenrollBatch.State.RUNNING).uuid
        return self._cancel(batch_id)

    @ddt.data(
        BulkUnenrollBatch.State.VALIDATED,
        BulkUnenrollBatch.State.PENDING,
        BulkUnenrollBatch.State.RUNNING,
    )
    def test_cancel_a_batch_still_in_flight(self, state):
        self._login(self.admin)
        batch = self._make_batch(state)
        resp = self._cancel(batch.uuid)
        self.assertIn(resp.status_code, (200, 202))
        batch.refresh_from_db()
        self.assertEqual(batch.state, "cancelled")

    def test_cancel_sweeps_unfinished_courses_and_keeps_finished_ones(self):
        """
        Unfinished rows left ``running`` would make a terminal batch read as still
        working; rows that already earned a terminal state keep it.
        """
        self._login(self.admin)
        batch = self._make_batch(BulkUnenrollBatch.State.RUNNING)
        rows = {}
        for i, state in enumerate([
            BulkUnenrollCourseState.State.SUCCEEDED,
            BulkUnenrollCourseState.State.FAILED,
            BulkUnenrollCourseState.State.RUNNING,
            BulkUnenrollCourseState.State.PENDING,
        ]):
            rows[state] = BulkUnenrollCourseState.objects.create(
                batch=batch,
                course_id=CourseOverviewFactory.create(org="edX", run=f"C{i}", display_name=f"C{i}").id,
                state=state,
            )

        resp = self._cancel(batch.uuid)
        self.assertIn(resp.status_code, (200, 202))

        for state, row in rows.items():
            row.refresh_from_db()

        self.assertEqual(rows[BulkUnenrollCourseState.State.RUNNING].state, "cancelled")
        self.assertEqual(rows[BulkUnenrollCourseState.State.PENDING].state, "cancelled")
        self.assertIsNotNone(rows[BulkUnenrollCourseState.State.RUNNING].finished)
        self.assertEqual(rows[BulkUnenrollCourseState.State.SUCCEEDED].state, "succeeded")
        self.assertEqual(rows[BulkUnenrollCourseState.State.FAILED].state, "failed")

    def test_cancelled_courses_count_as_finished(self):
        """The progress view's "N of M courses finished" must reach M after cancel."""
        self._login(self.admin)
        batch = self._make_batch(BulkUnenrollBatch.State.RUNNING)
        BulkUnenrollCourseState.objects.create(
            batch=batch,
            course_id=CourseOverviewFactory.create(org="edX", run="D0", display_name="D0").id,
            state=BulkUnenrollCourseState.State.RUNNING,
        )
        self._cancel(batch.uuid)

        status_url = reverse(
            "lms.djangoapps.support.rest_api:v1:bulk_unenroll_status",
            kwargs={"batch_id": str(batch.uuid)},
        )
        totals = self.client.get(status_url).json()["totals"]
        self.assertEqual(totals["courses_finished"], 1)

    @ddt.data(
        BulkUnenrollBatch.State.SUCCEEDED,
        BulkUnenrollBatch.State.FAILED,
        BulkUnenrollBatch.State.PARTIAL,
        BulkUnenrollBatch.State.CANCELLED,
    )
    def test_cancel_settled_batch_returns_409(self, state):
        self._login(self.admin)
        batch = self._make_batch(state)
        resp = self._cancel(batch.uuid)
        self.assertEqual(resp.status_code, 409)
        batch.refresh_from_db()
        self.assertEqual(batch.state, state)

    def test_cancel_does_not_overwrite_a_terminal_state_set_after_the_read(self):
        """
        A stale write must not resurrect a batch the engine just finalized.
        """
        self._login(self.admin)
        batch = self._make_batch(BulkUnenrollBatch.State.RUNNING)
        stale = BulkUnenrollBatch.objects.get(pk=batch.pk)          # says 'running'
        BulkUnenrollBatch.objects.filter(pk=batch.pk).update(state=BulkUnenrollBatch.State.SUCCEEDED)

        with patch.object(BulkUnenrollBatch.objects, "get", return_value=stale):
            resp = self._cancel(batch.uuid)

        self.assertEqual(resp.status_code, 409)
        batch.refresh_from_db()
        self.assertEqual(batch.state, "succeeded")


@ddt.ddt
class BulkUnenrollRetryAPIViewTest(BatchScopedEndpointTestMixin, SupportViewTestCase):
    """POST /api/support/v1/bulk_unenroll/<batch_id>/retry/."""

    url_name = "bulk_unenroll_retry"
    patch_dispatch = True

    def setUp(self):
        super().setUp()
        self.course_ok = CourseOverviewFactory.create(org="edX", run="OK", display_name="OK")
        self.course_bad = CourseOverviewFactory.create(org="edX", run="BAD", display_name="BAD")

    def _make_batch(self, state=BulkUnenrollBatch.State.PARTIAL, with_failure=True):
        """Build a batch with one succeeded course and, optionally, one failed."""
        batch = BulkUnenrollBatch.objects.create(
            requester=self.admin, total_courses=2, state=state,
        )
        BulkUnenrollCourseState.objects.create(
            batch=batch, course_id=self.course_ok.id,
            state=BulkUnenrollCourseState.State.SUCCEEDED, unenrolled=5,
            chunks_total=1, chunks_finished=1,
        )
        if with_failure:
            BulkUnenrollCourseState.objects.create(
                batch=batch, course_id=self.course_bad.id,
                state=BulkUnenrollCourseState.State.FAILED, failed_count=3,
                chunks_total=1, chunks_finished=1, error="boom",
            )
        return batch

    def _retry(self, batch_id):
        # See BulkUnenrollConfirmAPIViewTest._confirm — the hand-off is an on_commit hook.
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self._url(batch_id), {}, format="json")

    def _request(self, batch_id=None):
        return self._retry(self._make_batch().uuid if batch_id is None else batch_id)

    def test_retry_resets_failed_courses_and_redispatches(self):
        self._login(self.admin)
        batch = self._make_batch()
        resp = self._retry(batch.uuid)

        self.assertEqual(resp.status_code, 202)
        self.mock_dispatch.assert_called_once()
        batch.refresh_from_db()
        self.assertEqual(batch.state, "pending")
        failed = batch.courses.get(course_id=self.course_bad.id)
        self.assertEqual(failed.state, "pending")          # reset
        self.assertEqual(failed.failed_count, 0)
        self.assertEqual(failed.chunks_finished, 0)
        self.assertEqual(failed.error, "")
        ok = batch.courses.get(course_id=self.course_ok.id)
        self.assertEqual(ok.state, "succeeded")            # untouched
        self.assertEqual(ok.unenrolled, 5)

    def test_retry_with_no_failed_courses_returns_409(self):
        """
        A batch can be in a retryable *state* yet have no failed rows to re-run;
        dispatching would then queue nothing at all.
        """
        self._login(self.admin)
        batch = self._make_batch(state=BulkUnenrollBatch.State.PARTIAL, with_failure=False)
        resp = self._retry(batch.uuid)
        self.assertEqual(resp.status_code, 409)
        self.mock_dispatch.assert_not_called()

    @ddt.data(
        BulkUnenrollBatch.State.VALIDATED,
        BulkUnenrollBatch.State.PENDING,
        BulkUnenrollBatch.State.RUNNING,
        BulkUnenrollBatch.State.CANCELLED,
        BulkUnenrollBatch.State.SUCCEEDED,
    )
    def test_retry_unsettled_or_clean_batch_returns_409(self, state):
        self._login(self.admin)
        resp = self._retry(self._make_batch(state=state).uuid)
        self.assertEqual(resp.status_code, 409)
        self.mock_dispatch.assert_not_called()

    def test_failed_dispatch_restores_the_batch(self):
        self._login(self.admin)
        batch = self._make_batch()
        self.mock_dispatch.side_effect = OSError("broker unreachable")

        resp = self._retry(batch.uuid)

        self.assertEqual(resp.status_code, 202)
        batch.refresh_from_db()
        self.assertEqual(batch.state, "partial")     # retryable again
        failed = batch.courses.get(course_id=self.course_bad.id)
        self.assertEqual(failed.state, "failed")     # and the reset was rolled back
        # Including the diagnostics: a 'failed' row reporting no error and no counts
        # would strip the operator of the very information they retry against.
        self.assertEqual(failed.error, "boom")
        self.assertEqual(failed.failed_count, 3)
        self.assertEqual(failed.chunks_total, 1)
        self.assertEqual(failed.chunks_finished, 1)
        self.assertEqual(failed.attempt, 2)          # but the spent generation stays spent

    def test_failed_dispatch_does_not_claw_back_work_already_claimed(self):
        """
        A publish error does not prove non-delivery: if a dispatcher already claimed
        the courses, the rollback must not mark running work 'failed'.
        """
        self._login(self.admin)
        batch = self._make_batch()

        def raise_after_the_dispatcher_claimed_it(dispatched_batch):
            BulkUnenrollCourseState.objects.filter(
                batch=dispatched_batch, state=BulkUnenrollCourseState.State.PENDING,
            ).update(state=BulkUnenrollCourseState.State.RUNNING)
            BulkUnenrollBatch.objects.filter(pk=dispatched_batch.pk).update(
                state=BulkUnenrollBatch.State.RUNNING,
            )
            raise OSError("connection reset after publish")

        self.mock_dispatch.side_effect = raise_after_the_dispatcher_claimed_it
        resp = self._retry(batch.uuid)

        self.assertEqual(resp.status_code, 202)
        batch.refresh_from_db()
        self.assertEqual(batch.state, "running")     # the live run is left alone
        failed = batch.courses.get(course_id=self.course_bad.id)
        self.assertEqual(failed.state, "running")    # not clawed back to 'failed'

    def test_retry_starts_a_new_attempt_instead_of_reusing_chunk_identities(self):
        """
        The re-run fans out from index 0 again, so reusing (course, index) would let
        a straggler from the previous attempt squat the new chunk 0.
        """
        self._login(self.admin)
        batch = self._make_batch()
        failed_state = batch.courses.get(course_id=self.course_bad.id)
        ok_state = batch.courses.get(course_id=self.course_ok.id)
        old_chunk = BulkUnenrollChunk.objects.create(
            course_state=failed_state, chunk_index=0, attempt=1,
            state=BulkUnenrollChunk.State.FINISHED,
        )

        resp = self._retry(batch.uuid)

        self.assertEqual(resp.status_code, 202)
        failed_state.refresh_from_db()
        ok_state.refresh_from_db()
        self.assertEqual(failed_state.attempt, 2)      # re-run under a fresh generation
        self.assertEqual(ok_state.attempt, 1)          # untouched; it is not re-run
        # The old row survives as history: the new attempt's chunk 0 is a new identity.
        self.assertTrue(BulkUnenrollChunk.objects.filter(pk=old_chunk.pk).exists())
        BulkUnenrollChunk.objects.create(course_state=failed_state, chunk_index=0, attempt=2)

    def test_concurrent_retry_does_not_double_dispatch(self):
        self._login(self.admin)
        batch = self._make_batch()
        stale = BulkUnenrollBatch.objects.get(pk=batch.pk)          # says 'partial'
        BulkUnenrollBatch.objects.filter(pk=batch.pk).update(state=BulkUnenrollBatch.State.PENDING)

        with patch.object(BulkUnenrollBatch.objects, "get", return_value=stale):
            resp = self._retry(batch.uuid)

        self.assertEqual(resp.status_code, 409)
        self.mock_dispatch.assert_not_called()


class BulkUnenrollDispatchCommitTest(TransactionTestCase):
    """
    The engine hand-off must happen with no transaction open, or a worker can read a
    batch row the request has not committed and drop it as "not dispatchable".

    ``TransactionTestCase``, not ``TestCase``: under ``ATOMIC_REQUESTS`` the latter
    never commits, so ``in_atomic_block`` would be ``True`` regardless and the
    ``on_commit`` hooks would not run at all.
    """

    def setUp(self):
        super().setUp()
        self.admin = AdminFactory()
        self.course = CourseOverviewFactory.create(org="edX", run="TX", display_name="TX")
        self.client = APIClient()
        self.client.login(username=self.admin.username, password=TEST_PASSWORD)
        patcher = patch("lms.djangoapps.support.rest_api.v1.views._dispatch_bulk_unenroll")
        self.mock_dispatch = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_dispatch.side_effect = lambda *a, **k: self.seen.update(
            in_atomic_block=transaction.get_connection().in_atomic_block,
        )
        self.seen = {}

    def _url(self, name, batch_id):
        return reverse(f"lms.djangoapps.support.rest_api:v1:{name}", kwargs={"batch_id": str(batch_id)})

    def _batch(self, state, course_state):
        """A one-course batch in the given batch/course states."""
        batch = BulkUnenrollBatch.objects.create(
            requester=self.admin, csv_filename="courses.csv", total_courses=1, state=state,
        )
        BulkUnenrollCourseState.objects.create(
            batch=batch, course_id=self.course.id, active_count=3, state=course_state,
            chunks_total=1, chunks_finished=1, error="boom",
        )
        return batch

    def assert_dispatched_after_commit(self, response):
        self.assertEqual(response.status_code, 202)
        self.mock_dispatch.assert_called_once()
        self.assertFalse(
            self.seen["in_atomic_block"],
            "dispatched inside an open transaction: the worker can read a row this "
            "request has not committed",
        )

    def test_confirm_dispatches_only_after_the_flip_is_committed(self):
        batch = self._batch(BulkUnenrollBatch.State.VALIDATED, BulkUnenrollCourseState.State.PENDING)
        self.assert_dispatched_after_commit(
            self.client.post(self._url("bulk_unenroll_confirm", batch.uuid), {"reason": "cleanup"}, format="json")
        )

    def test_retry_dispatches_only_after_the_reset_is_committed(self):
        batch = self._batch(BulkUnenrollBatch.State.PARTIAL, BulkUnenrollCourseState.State.FAILED)
        self.assert_dispatched_after_commit(
            self.client.post(self._url("bulk_unenroll_retry", batch.uuid), {}, format="json")
        )
