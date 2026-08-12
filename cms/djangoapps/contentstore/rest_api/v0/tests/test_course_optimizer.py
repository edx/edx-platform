"""
Unit tests for course optimizer
"""
from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.test import TestCase
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from django.urls import reverse
from rest_framework import status

from cms.djangoapps.contentstore.tests.test_utils import AuthorizeStaffTestCase
from cms.djangoapps.contentstore.tests.utils import CourseTestCase


class TestGetLinkCheckStatus(AuthorizeStaffTestCase, ModuleStoreTestCase, TestCase):
    '''
    Authentication and Authorization Tests for CourseOptimizer.
    For concrete tests that are run, check `AuthorizeStaffTestCase`.
    '''
    def make_request(self, course_id=None, data=None, **kwargs):
        url = self.get_url(self.course.id)
        response = self.client.get(url, data)
        return response

    def get_url(self, course_key):
        url = reverse(
            'cms.djangoapps.contentstore:v0:link_check_status',
            kwargs={'course_id': self.course.id}
        )
        return url

    def test_produces_4xx_when_invalid_course_id(self):
        '''
        Test course_id validation
        '''
        response = self.make_request(course_id='invalid_course_id')
        self.assertIn(response.status_code, range(400, 500))

    def test_produces_4xx_when_additional_kwargs(self):
        '''
        Test additional kwargs validation
        '''
        response = self.make_request(course_id=self.course.id, malicious_kwarg='malicious_kwarg')
        self.assertIn(response.status_code, range(400, 500))


class TestPostLinkCheck(AuthorizeStaffTestCase, ModuleStoreTestCase, TestCase):
    '''
    Authentication and Authorization Tests for CourseOptimizer.
    For concrete tests that are run, check `AuthorizeStaffTestCase`.
    '''
    def make_request(self, course_id=None, data=None, **kwargs):
        url = self.get_url(self.course.id)
        response = self.client.post(url, data)
        return response

    def get_url(self, course_key):
        url = reverse(
            'cms.djangoapps.contentstore:v0:link_check',
            kwargs={'course_id': self.course.id}
        )
        return url

    def test_produces_4xx_when_invalid_course_id(self):
        '''
        Test course_id validation
        '''
        response = self.make_request(course_id='invalid_course_id')
        self.assertIn(response.status_code, range(400, 500))

    def test_produces_4xx_when_additional_kwargs(self):
        '''
        Test additional kwargs validation
        '''
        response = self.make_request(course_id=self.course.id, malicious_kwarg='malicious_kwarg')
        self.assertIn(response.status_code, range(400, 500))

    def test_produces_4xx_when_unexpected_data(self):
        '''
        Test validation when request contains unexpected data
        '''
        response = self.make_request(course_id=self.course.id, data={'unexpected_data': 'unexpected_data'})
        self.assertIn(response.status_code, range(400, 500))


class CourseAnalysisReportViewTest(CourseTestCase):
    """
    Tests for CourseAnalysisReportView, which kicks off a Course Optimizer
    extended-analysis run by generating a course export server-side and
    handing it to the xpert-ai-workflows backend.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            'cms.djangoapps.contentstore:v0:course_analysis_report',
            kwargs={'course_id': str(self.course.id)},
        )
        self.export_patch = (
            'cms.djangoapps.contentstore.rest_api.v0.views.course_optimizer.create_export_tarball'
        )
        self.backend_post_patch = (
            'cms.djangoapps.contentstore.rest_api.v0.views.course_optimizer.requests.post'
        )

    def _mock_tarball(self):
        tarball = Mock()
        tarball.name = '/tmp/whatever.tar.gz'
        return tarball

    def test_unauthenticated(self):
        self.client.logout()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_no_course_access(self):
        client, _ = self.create_non_staff_authed_user_client()
        response = client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_kicks_off_backend_run(self):
        with patch(self.export_patch) as mock_export, patch(self.backend_post_patch) as mock_post:
            mock_export.return_value = self._mock_tarball()
            mock_post.return_value = Mock(
                status_code=202,
                json=Mock(return_value={'run_id': 'run-123'}),
            )
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.json(), {'run_id': 'run-123'})
        self.assertEqual(
            mock_post.call_args.kwargs['headers']['X-Api-Key'],
            settings.COURSE_ANALYSIS_WORKFLOW_API_KEY,
        )

    def test_backend_unreachable_returns_502(self):
        with patch(self.export_patch) as mock_export, patch(self.backend_post_patch) as mock_post:
            mock_export.return_value = self._mock_tarball()
            mock_post.side_effect = requests.ConnectionError()
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)


class CourseAnalysisReportStatusViewTest(CourseTestCase):
    """
    Tests for CourseAnalysisReportStatusView, the Studio-side proxy for a
    course's latest Course Optimizer extended-analysis run status
    (xpert-ai-workflows).
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            'cms.djangoapps.contentstore:v0:course_analysis_report_status',
            kwargs={'course_id': str(self.course.id)},
        )
        self.backend_get_patch = (
            'cms.djangoapps.contentstore.rest_api.v0.views.course_optimizer.requests.get'
        )

    def test_unauthenticated(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_no_course_access(self):
        client, _ = self.create_non_staff_authed_user_client()
        response = client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

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

    def test_no_runs_yet_returns_404(self):
        with patch(self.backend_get_patch) as mock_get:
            mock_get.return_value = Mock(
                status_code=404,
                json=Mock(return_value={'detail': "No runs found for course"}),
            )
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_backend_unreachable_returns_502(self):
        with patch(self.backend_get_patch) as mock_get:
            mock_get.side_effect = requests.ConnectionError()
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
