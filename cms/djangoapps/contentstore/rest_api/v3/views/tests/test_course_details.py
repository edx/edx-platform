"""
Unit tests for CourseDetailsViewSet (v3).

Single test module covering every ADR applied to the viewset:
  * ADR 0025 / 0026 / 0027 / 0028 — action + permission + routing tests
    (``TestCourseDetailsViewSetPermissions``, ``TestCourseDetailsViewSetActions``)
  * ADR 0029 — standardized error envelope shape tests
    (``TestCourseDetailsViewSetErrorShape``)

MongoDB-free: every service-layer call (``CourseDetails.fetch``,
``modulestore``, ``update_course_details``,
``openedx_authz.user_has_course_permission``, and
``CourseOverview.course_exists``) is mocked so the suite runs without a live
modulestore or course-overview row.

``patch.object`` is used for ``serializer_class`` because the attribute is
resolved at class-definition time — string-based ``patch()`` of the module
attribute does not replace the live ViewSet attribute.
"""
from unittest.mock import MagicMock, patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from cms.djangoapps.contentstore.rest_api.v3.views.course_details import CourseDetailsViewSet
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

# ---------------------------------------------------------------------------
# Mock target paths
# ---------------------------------------------------------------------------
MOCK_FETCH = "openedx.core.djangoapps.models.course_details.CourseDetails.fetch"
MOCK_MODULESTORE = "cms.djangoapps.contentstore.rest_api.v3.views.course_details.modulestore"
MOCK_UPDATE = "cms.djangoapps.contentstore.rest_api.v3.views.course_details.update_course_details"
MOCK_HAS_PERMISSION = (
    "cms.djangoapps.contentstore.rest_api.v3.views.course_details.user_has_course_permission"
)
MOCK_CLASSIFY = "cms.djangoapps.contentstore.rest_api.v3.views.course_details._classify_update"
MOCK_COURSE_EXISTS = (
    "cms.djangoapps.contentstore.rest_api.v3.views.course_details.CourseOverview.course_exists"
)

# Syntactically valid course key reused across action / permission / envelope tests.
TEST_COURSE_ID = "course-v1:org+course+run"

_REQUIRED_ERROR_FIELDS = ("type", "title", "status", "detail", "instance")


# ===========================================================================
# ADR 0026 — permission boundary tests
# ===========================================================================
class TestCourseDetailsViewSetPermissions(APITestCase):
    """
    ADR 0026 – permission regression tests for CourseDetailsViewSet (v3).

    The v3 viewset enforces ``IsAuthenticated`` at the class level and uses
    inline ``user_has_course_permission`` checks for course-level authorization
    (necessary because the schedule-vs-details split depends on the payload).
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v3:course_details-detail",
            kwargs={"course_id": TEST_COURSE_ID},
        )

    # --- Unauthenticated ---

    def test_unauthenticated_get_returns_401(self):
        """Unauthenticated GET must return 401 (IsAuthenticated)."""
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unauthenticated_put_returns_401(self):
        """Unauthenticated PUT must return 401 (IsAuthenticated)."""
        response = self.client.put(self.url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    # --- Authenticated but no course access ---

    @patch.object(CourseOverview, "course_exists", return_value=True)
    @patch(MOCK_HAS_PERMISSION, return_value=False)
    def test_non_author_get_returns_403(self, mock_perm, mock_exists):  # noqa: ARG002
        """Authenticated user without view permission must receive 403 on GET."""
        user = UserFactory.create()
        self.client.force_authenticate(user=user)
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch.object(CourseOverview, "course_exists", return_value=True)
    @patch(MOCK_HAS_PERMISSION, return_value=False)
    @patch(MOCK_CLASSIFY, return_value=(False, True))  # details-only update
    def test_non_author_put_returns_403(self, mock_classify, mock_perm, mock_exists):  # noqa: ARG002
        """Authenticated user without edit permission must receive 403 on PUT."""
        user = UserFactory.create()
        self.client.force_authenticate(user=user)
        response = self.client.put(self.url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ===========================================================================
# ADR 0025 / 0028 — action body tests
# ===========================================================================
class TestCourseDetailsViewSetActions(APITestCase):
    """
    Action tests for CourseDetailsViewSet (retrieve and update).

    Service-layer calls are mocked, and ``user_has_course_permission`` returns
    True so authorization passes through to the action body.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.client.force_authenticate(user=self.user)
        self.url = reverse(
            "cms.djangoapps.contentstore:v3:course_details-detail",
            kwargs={"course_id": TEST_COURSE_ID},
        )

    @patch.object(CourseDetailsViewSet, "serializer_class")
    @patch.object(CourseOverview, "course_exists", return_value=True)
    @patch(MOCK_HAS_PERMISSION, return_value=True)
    @patch(MOCK_FETCH)
    def test_retrieve_calls_course_details_fetch(
        self, mock_fetch, mock_perm, mock_exists, mock_ser_cls,  # noqa: ARG002
    ):
        """GET calls CourseDetails.fetch() and returns 200."""
        mock_fetch.return_value = MagicMock()
        mock_ser_cls.return_value.data = {"course_id": "run"}

        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        mock_fetch.assert_called_once()

    @patch.object(CourseDetailsViewSet, "serializer_class")
    @patch.object(CourseOverview, "course_exists", return_value=True)
    @patch(MOCK_HAS_PERMISSION, return_value=True)
    @patch(MOCK_UPDATE)
    @patch(MOCK_MODULESTORE)
    @patch(MOCK_CLASSIFY, return_value=(False, True))
    def test_update_calls_update_course_details(  # noqa: PLR0913
        self,
        mock_classify,  # noqa: ARG002
        mock_store,
        mock_update,
        mock_perm,  # noqa: ARG002
        mock_exists,  # noqa: ARG002
        mock_ser_cls,
    ):
        """PUT calls update_course_details() and returns 200."""
        mock_store.return_value.get_course.return_value = MagicMock()
        mock_update.return_value = MagicMock()
        mock_ser_cls.return_value.data = {"course_id": "run"}

        response = self.client.put(self.url, data={}, content_type="application/json")

        assert response.status_code == status.HTTP_200_OK
        mock_update.assert_called_once()


# ===========================================================================
# ADR 0029 — standardized error-response envelope tests
# ===========================================================================
class TestCourseDetailsViewSetErrorShape(APITestCase):
    """
    ADR 0029 – error response shape regression tests for CourseDetailsViewSet (v3).

    The envelope is wired in via
    :class:`openedx.core.lib.api.mixins.StandardizedErrorMixin`, which overrides
    DRF's per-view ``get_exception_handler`` to point at
    ``openedx.core.lib.api.exceptions.standardized_error_exception_handler``.

    Scoped to v3 — the project-wide DRF ``EXCEPTION_HANDLER`` setting is
    unchanged, so v0 / v1 / v2 / v4 endpoints continue to return the legacy
    error shape (locked in by ``test_v1_endpoint_unaffected_by_v3_envelope``).
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.detail_url = reverse(
            "cms.djangoapps.contentstore:v3:course_details-detail",
            kwargs={"course_id": TEST_COURSE_ID},
        )

    def test_unauthenticated_get_returns_standardized_401(self):
        """Unauthenticated GET must return 401 with the ADR 0029 envelope."""
        response = self.client.get(self.detail_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in response.data, f"ADR 0029: missing field '{field}'"

    def test_unauthenticated_401_type_uri(self):
        """The ``type`` field for 401 must be the ADR 0029 authn URI."""
        response = self.client.get(self.detail_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data.get("type") == "https://docs.openedx.org/errors/authn"

    @patch(MOCK_COURSE_EXISTS, return_value=True)
    @patch(MOCK_HAS_PERMISSION, return_value=False)
    def test_non_author_get_returns_standardized_403(self, mock_perm, mock_exists):  # noqa: ARG002
        """Authenticated non-author GET must return 403 with the ADR 0029 envelope."""
        non_author = UserFactory.create()
        self.client.force_authenticate(user=non_author)
        response = self.client.get(self.detail_url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in response.data, f"ADR 0029: missing field '{field}'"

    @patch(MOCK_COURSE_EXISTS, return_value=True)
    @patch(MOCK_HAS_PERMISSION, return_value=False)
    def test_non_author_403_type_uri(self, mock_perm, mock_exists):  # noqa: ARG002
        """The ``type`` field for 403 must be the ADR 0029 authz URI."""
        non_author = UserFactory.create()
        self.client.force_authenticate(user=non_author)
        response = self.client.get(self.detail_url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data.get("type") == "https://docs.openedx.org/errors/authz"

    @patch(MOCK_COURSE_EXISTS, return_value=False)
    def test_nonexistent_course_returns_standardized_404(self, mock_exists):  # noqa: ARG002
        """GET for a non-existent course must return 404 with the ADR 0029 envelope."""
        staff = UserFactory.create(is_staff=True)
        self.client.force_authenticate(user=staff)
        response = self.client.get(self.detail_url)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in response.data, f"ADR 0029: missing field '{field}'"

    @patch(MOCK_COURSE_EXISTS, return_value=False)
    def test_not_found_type_uri(self, mock_exists):  # noqa: ARG002
        """The ``type`` field for 404 must be the ADR 0029 not-found URI."""
        staff = UserFactory.create(is_staff=True)
        self.client.force_authenticate(user=staff)
        response = self.client.get(self.detail_url)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data.get("type") == "https://docs.openedx.org/errors/not-found"

    def test_error_body_has_no_developer_message(self):
        """Error responses must NOT contain old DeveloperErrorViewMixin fields."""
        response = self.client.get(self.detail_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "developer_message" not in response.data
        assert "error_code" not in response.data

    def test_instance_field_is_request_path(self):
        """The ``instance`` field must equal the request path."""
        response = self.client.get(self.detail_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data.get("instance") == self.detail_url

    def test_v1_endpoint_unaffected_by_v3_envelope(self):
        """
        The ADR 0029 envelope must be scoped to v3 — hitting the legacy v1
        ``course_details`` endpoint unauthenticated must NOT return the v3
        envelope (no ``type`` / ``instance`` keys).
        """
        v1_url = reverse(
            "cms.djangoapps.contentstore:v1:course_details",
            kwargs={"course_id": TEST_COURSE_ID},
        )
        response = self.client.get(v1_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "type" not in response.data
        assert "instance" not in response.data
