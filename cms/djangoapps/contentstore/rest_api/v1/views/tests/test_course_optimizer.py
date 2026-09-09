"""
Unit tests for the Course Optimizer extended-analysis report views
"""
from unittest.mock import Mock, patch

import requests
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from rest_framework import status

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.toggles import ENABLE_COURSE_OPTIMIZER_EXTENDED_CHECKS


class CourseAnalysisReportViewTest(CourseTestCase):
    """
    Tests for CourseAnalysisReportView, which queues a background task to
    generate a course export and hand it to the xpert-ai-workflows backend
    to kick off a Course Optimizer extended-analysis run.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            'cms.djangoapps.contentstore:v1:course_analysis_report',
            kwargs={'course_id': str(self.course.id)},
        )
        self.task_patch = (
            'cms.djangoapps.contentstore.rest_api.v1.views.course_optimizer.submit_course_analysis_report'
        )

    def test_unauthenticated(self):
        self.client.logout()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_no_course_access(self):
        client, _ = self.create_non_staff_authed_user_client()
        response = client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_waffle_flag_disabled_returns_400(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_waffle_flag(ENABLE_COURSE_OPTIMIZER_EXTENDED_CHECKS, True)
    def test_queues_background_task_and_returns_immediately(self):
        with patch(self.task_patch) as mock_task:
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.json(), {'status': 'pending'})
        mock_task.delay.assert_called_once_with(str(self.course.id))


class CourseAnalysisReportStatusViewTest(CourseTestCase):
    """
    Tests for CourseAnalysisReportStatusView, the Studio-side proxy for a
    course's latest Course Optimizer extended-analysis run status
    (xpert-ai-workflows).
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            'cms.djangoapps.contentstore:v1:course_analysis_report_status',
            kwargs={'course_id': str(self.course.id)},
        )
        self.backend_get_patch = (
            'cms.djangoapps.contentstore.rest_api.v1.views.course_optimizer.requests.get'
        )

    def test_unauthenticated(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_no_course_access(self):
        client, _ = self.create_non_staff_authed_user_client()
        response = client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_waffle_flag_disabled_returns_400(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_waffle_flag(ENABLE_COURSE_OPTIMIZER_EXTENDED_CHECKS, True)
    def test_proxies_backend_response(self):
        with patch(self.backend_get_patch) as mock_get:
            mock_get.return_value = Mock(
                status_code=200,
                json=Mock(return_value={
                    'run_id': 'run-123', 'status': 'COMPLETE', 'report': {}, 'error': None,
                }),
            )
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {
            'run_id': 'run-123', 'status': 'COMPLETE', 'report': {}, 'error': None,
        })

    @override_waffle_flag(ENABLE_COURSE_OPTIMIZER_EXTENDED_CHECKS, True)
    def test_no_runs_yet_returns_404(self):
        with patch(self.backend_get_patch) as mock_get:
            mock_get.return_value = Mock(
                status_code=404,
                json=Mock(return_value={'detail': "No runs found for course"}),
            )
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @override_waffle_flag(ENABLE_COURSE_OPTIMIZER_EXTENDED_CHECKS, True)
    def test_backend_unreachable_returns_502(self):
        with patch(self.backend_get_patch) as mock_get:
            mock_get.side_effect = requests.ConnectionError()
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)

    @override_waffle_flag(ENABLE_COURSE_OPTIMIZER_EXTENDED_CHECKS, True)
    def test_backend_returns_invalid_json_returns_502(self):
        with patch(self.backend_get_patch) as mock_get:
            mock_get.return_value = Mock(
                status_code=200,
                json=Mock(side_effect=ValueError()),
            )
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)

    @override_waffle_flag(ENABLE_COURSE_OPTIMIZER_EXTENDED_CHECKS, True)
    def test_produces_404_when_course_does_not_exist(self):
        url = reverse(
            'cms.djangoapps.contentstore:v1:course_analysis_report_status',
            kwargs={'course_id': 'course-v1:edX+DemoX+Nonexistent_Course'},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
