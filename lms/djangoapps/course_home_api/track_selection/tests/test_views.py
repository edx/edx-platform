"""
Tests for Track Selection API in the Course Home API.
"""

import ddt
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.course_home_api.tests.utils import BaseCourseHomeTests
from lms.djangoapps.course_home_api.toggles import COURSE_HOME_MICROFRONTEND_TRACK_SELECTION


@ddt.ddt
@override_waffle_flag(COURSE_HOME_MICROFRONTEND_TRACK_SELECTION, active=True)
class TrackSelectionTestViews(BaseCourseHomeTests):
    """
    Tests for the Track Selection BFF API.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse('course-home:track-selection-tab', args=[self.course.id])

    def test_get_authenticated_audit_enrolled_user(self):
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['course_modes_choose_url'] == reverse(
            'course_modes_choose',
            kwargs={'course_id': str(self.course.id)},
        )
        assert response.data['verified_mode'] is not None
        assert response.data['audit_mode'] is not None
        assert response.data['course_name'] == self.course.display_name_with_default

    def test_get_authenticated_verified_enrolled_user_redirects(self):
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert 'redirect_url' in response.data
        assert response.data['redirect_url'].endswith(f'/course/{self.course.id}/home')

    def test_get_authenticated_unenrolled_user_with_open_enrollment(self):
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['course_modes_choose_url'] is not None

    def test_get_authenticated_previously_enrolled_user(self):
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        CourseEnrollment.unenroll(self.user, self.course.id)
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['course_modes_choose_url'] is not None

    def test_get_unauthenticated_user(self):
        self.client.logout()
        response = self.client.get(self.url)
        assert response.status_code == 401

    def test_get_unknown_course_redirects_to_legacy_choose(self):
        url = reverse('course-home:track-selection-tab', args=['course-v1:unknown+course+2T2020'])
        response = self.client.get(url)
        assert response.status_code == 200
        assert 'redirect_url' in response.data
        assert response.data['redirect_url'].endswith('/course_modes/choose/course-v1:unknown+course+2T2020/')

    @override_waffle_flag(COURSE_HOME_MICROFRONTEND_TRACK_SELECTION, active=False)
    def test_waffle_flag_disabled(self):
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        response = self.client.get(self.url)
        assert response.status_code == 404
