"""API Views for the Course Optimizer extended-analysis report."""

import os

import edx_api_doc_tools as apidocs
import requests
from django.conf import settings
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.djangoapps.contentstore.tasks import create_export_tarball
from cms.djangoapps.contentstore.toggles import enable_course_optimizer_extended_checks
from common.djangoapps.student.auth import has_course_author_access
from common.djangoapps.util.json_request import JsonResponse
from openedx.core.lib.api.view_utils import (
    DeveloperErrorViewMixin,
    verify_course_exists,
    view_auth_classes,
)
from xmodule.modulestore.django import modulestore


@view_auth_classes(is_authenticated=True)
class CourseAnalysisReportView(DeveloperErrorViewMixin, APIView):
    """
    View for kicking off a Course Optimizer extended-analysis run.
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID"),
        ],
        responses={
            202: "Analysis run queued.",
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
            502: "The Course Optimizer extended-report backend is unreachable.",
        },
    )
    @verify_course_exists()
    def post(self, request: Request, course_id: str):
        """
        Generate a fresh export of the course and hand it to the Course
        Optimizer extended-report backend (xpert-ai-workflows) to start a
        new analysis run. Studio generates the export server-side -- the
        browser never uploads anything or talks to that backend directly.

        **Example Request**

            POST /api/contentstore/v1/course_optimizer/analysis/{course_id}

        **Response Values**
        ```json
        {
            "run_id": <string>
        }
        ```
        """
        course_key = CourseKey.from_string(course_id)
        if not has_course_author_access(request.user, course_key):
            self.permission_denied(request)

        if not enable_course_optimizer_extended_checks(course_key):
            return JsonResponse(
                {"error": "Course optimizer extended checks are not enabled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        course_block = modulestore().get_course(course_key)
        tarball = create_export_tarball(course_block, course_key, {})
        try:
            tarball.seek(0)
            try:
                response = requests.post(
                    f'{settings.COURSE_ANALYSIS_WORKFLOW_URL}/courses/{course_id}/runs',
                    files={'file': (os.path.basename(tarball.name), tarball, 'application/gzip')},
                    headers={'X-Api-Key': settings.COURSE_ANALYSIS_WORKFLOW_API_KEY},
                    timeout=settings.COURSE_ANALYSIS_WORKFLOW_REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException:
                return Response(status=status.HTTP_502_BAD_GATEWAY)
        finally:
            tarball.close()

        try:
            response_data = response.json()
        except ValueError:
            return Response(status=status.HTTP_502_BAD_GATEWAY)

        return Response(response_data, status=response.status_code)


@view_auth_classes()
class CourseAnalysisReportStatusView(DeveloperErrorViewMixin, APIView):
    """
    View proxying a course's Course Optimizer extended-report status.

    Studio calls the Course Optimizer extended-report backend
    (xpert-ai-workflows) server-side and returns its response as-is; the
    browser never calls that backend directly.
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID"),
        ],
        responses={
            200: "OK",
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The course has no analysis runs yet.",
            502: "The Course Optimizer extended-report backend is unreachable.",
        },
    )
    @verify_course_exists()
    def get(self, request: Request, course_id: str):
        """
        Proxy the status of a course's most recent Course Optimizer
        extended-analysis run.

        **Example Request**

            GET /api/contentstore/v1/course_optimizer/analysis/{course_id}/status

        **Response Values**

            The xpert-ai-workflows run-status response, passed through
            unchanged: `{run_id, status, report, error}`. A 404 means the
            course has no analysis runs yet.
        """
        course_key = CourseKey.from_string(course_id)
        if not has_course_author_access(request.user, course_key):
            self.permission_denied(request)

        if not enable_course_optimizer_extended_checks(course_key):
            return JsonResponse(
                {"error": "Course optimizer extended checks are not enabled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            response = requests.get(
                f'{settings.COURSE_ANALYSIS_WORKFLOW_URL}/courses/{course_id}/runs/latest',
                headers={'X-Api-Key': settings.COURSE_ANALYSIS_WORKFLOW_API_KEY},
                timeout=settings.COURSE_ANALYSIS_WORKFLOW_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            return Response(status=status.HTTP_502_BAD_GATEWAY)

        try:
            response_data = response.json()
        except ValueError:
            return Response(status=status.HTTP_502_BAD_GATEWAY)

        return Response(response_data, status=response.status_code)
