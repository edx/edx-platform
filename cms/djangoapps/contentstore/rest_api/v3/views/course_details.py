"""
API Views for course details — v3.

This module is the v3 incarnation of the v1 ``course_details`` endpoint,
restructured to apply the FC-0118 ADRs:

  * ADR 0025 – ``serializer_class`` on the viewset
  * ADR 0026 – explicit ``authentication_classes`` + ``permission_classes``
  * ADR 0027 – ``drf_spectacular`` for OpenAPI schema generation
  * ADR 0028 – consolidated into a single DRF ``ViewSet`` registered via
    ``DefaultRouter`` (replaces ``CourseDetailsView`` ``APIView``)
  * ADR 0029 – standardized error envelope via :class:`StandardizedErrorMixin`
    (v3-scoped — does not change the project-wide DRF ``EXCEPTION_HANDLER``
    setting)

Permission model note:
    PR #38365 proposed a class-level ``HasStudioReadAccess`` permission. The
    current v1 view has since evolved to use the ``openedx_authz`` permission
    framework with a schedule-vs-details classification that gates updates on
    *different* permissions depending on the payload. That granularity cannot
    be hoisted to a single class-level permission, so the per-action checks
    remain inline (gated by ``IsAuthenticated`` at the class level).
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import OpenApiParameter, OpenApiRequest, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from openedx_authz.constants.permissions import (
    COURSES_EDIT_DETAILS,
    COURSES_EDIT_SCHEDULE,
    COURSES_VIEW_SCHEDULE_AND_DETAILS,
)
from rest_framework import viewsets
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v1.serializers import CourseDetailsSerializer
from cms.djangoapps.contentstore.rest_api.v1.views.course_details import _classify_update
from cms.djangoapps.contentstore.utils import update_course_details
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import user_has_course_permission
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.models.course_details import CourseDetails
from openedx.core.lib.api.mixins import StandardizedErrorMixin
from xmodule.modulestore.django import modulestore

_COURSE_ID_PARAMETER = OpenApiParameter(
    name="course_id",
    description="Course ID",
    required=True,
    type=str,
    location=OpenApiParameter.PATH,
)
_COMMON_ERROR_RESPONSES = {
    401: OpenApiResponse(description="The requester is not authenticated."),
    403: OpenApiResponse(description="The requester cannot access the specified course."),
    404: OpenApiResponse(description="The requested course does not exist."),
}


def _resolve_course_key(course_id: str) -> CourseKey:
    """
    Parse ``course_id`` into a ``CourseKey`` and verify the course exists.

    Raises ``NotFound`` for both unparseable keys and missing courses, which
    the ADR 0029 envelope renders as a structured 404 response. This replaces
    the legacy ``@verify_course_exists()`` decorator from v1 and avoids
    relying on ``DeveloperErrorViewMixin``.
    """
    try:
        course_key = CourseKey.from_string(course_id)
    except InvalidKeyError as exc:
        raise NotFound("The provided course key cannot be parsed.") from exc
    if not CourseOverview.course_exists(course_key):
        raise NotFound(f"Course {course_id} not found.")
    return course_key


class CourseDetailsViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    ViewSet for course details (v3). Registered via DefaultRouter (basename ``course_details``).

    Router-generated URLs::

        GET  /api/contentstore/v3/course_details/{course_id}/  → retrieve
        PUT  /api/contentstore/v3/course_details/{course_id}/  → update

    Supersedes ``CourseDetailsView`` at ``/api/contentstore/v1/course_details/{course_id}``.
    """

    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated,)
    serializer_class = CourseDetailsSerializer

    # Matches both slash-separated (org/course/run) and plus-separated (course-v1:org+course+run) IDs
    lookup_field = "course_id"
    lookup_value_regex = r"[^/+]+(?:/|\+)[^/+]+(?:/|\+)[^/?]+"

    @extend_schema(
        summary="Retrieve a course's details",
        description="Get an object containing all the course details for the specified course.",
        parameters=[_COURSE_ID_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=CourseDetailsSerializer,
                description="Course details retrieved successfully.",
            ),
            **_COMMON_ERROR_RESPONSES,
        },
    )
    def retrieve(self, request: Request, course_id: str):
        """
        Get an object containing all the course details.

        **Example Request**

            GET /api/contentstore/v3/course_details/{course_id}/
        """
        course_key = _resolve_course_key(course_id)
        if not user_has_course_permission(
            request.user,
            COURSES_VIEW_SCHEDULE_AND_DETAILS.identifier,
            course_key,
            LegacyAuthoringPermission.READ,
        ):
            self.permission_denied(request)

        course_details = CourseDetails.fetch(course_key)
        serializer = self.serializer_class(course_details)
        return Response(serializer.data)

    @extend_schema(
        summary="Update a course's details",
        description="Update the details for the specified course.",
        request=OpenApiRequest(request=CourseDetailsSerializer),
        parameters=[_COURSE_ID_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=CourseDetailsSerializer,
                description="Course details updated successfully.",
            ),
            400: OpenApiResponse(description="Bad request — invalid data."),
            **_COMMON_ERROR_RESPONSES,
        },
    )
    def update(self, request: Request, course_id: str):
        """
        Update a course's details.

        **Example Request**

            PUT /api/contentstore/v3/course_details/{course_id}/

        **PUT Parameters**

        The data sent for a put request should follow a similar format as
        is returned by a ``GET`` request. Multiple details can be updated in
        a single request, however only the ``value`` field can be updated;
        any other fields, if included, will be ignored.

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned,
        along with all the course's details similar to a ``GET`` request.
        """
        course_key = _resolve_course_key(course_id)
        is_schedule_update, is_details_update = _classify_update(request.data, course_key)

        if not is_schedule_update and not is_details_update:
            # No updatable fields provided — fall through to a details-permission check
            # so the caller gets 403 if they lack edit-details rights.
            is_details_update = True

        if is_schedule_update and not user_has_course_permission(
            request.user,
            COURSES_EDIT_SCHEDULE.identifier,
            course_key,
            LegacyAuthoringPermission.READ,
        ):
            self.permission_denied(request)

        if is_details_update and not user_has_course_permission(
            request.user,
            COURSES_EDIT_DETAILS.identifier,
            course_key,
            LegacyAuthoringPermission.READ,
        ):
            self.permission_denied(request)

        course_block = modulestore().get_course(course_key)

        try:
            updated_data = update_course_details(request, course_key, request.data, course_block)
        except DjangoValidationError as err:
            raise DRFValidationError(err.message) from err

        serializer = self.serializer_class(updated_data)
        return Response(serializer.data)
