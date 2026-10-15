"""
Tests access.py
"""


from django.conf import settings
from django.test import TestCase
from edx_rest_framework_extensions.auth.jwt.cookies import jwt_cookie_name
from opaque_keys.edx.locator import CourseLocator

from cms.djangoapps.contentstore.tests.utils import AjaxEnabledTestClient, CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url, reverse_usage_url
from common.djangoapps.student.auth import add_users
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import BlockFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..access import get_user_role


class RolesTest(TestCase):
    """
    Tests for lti user role serialization.
    """
    def setUp(self):
        """ Test case setup """
        super().setUp()

        self.global_admin = AdminFactory()
        self.instructor = UserFactory.create(
            username='testinstructor',
            email='testinstructor+courses@edx.org',
            password='foo',
        )
        self.staff = UserFactory.create(
            username='teststaff',
            email='teststaff+courses@edx.org',
            password='foo',
        )
        self.course_key = CourseLocator('mitX', '101', 'test')

    def test_get_user_role_instructor(self):
        """
        Verifies if user is instructor.
        """
        add_users(self.global_admin, CourseInstructorRole(self.course_key), self.instructor)
        self.assertEqual(
            'instructor',
            get_user_role(self.instructor, self.course_key)
        )
        add_users(self.global_admin, CourseStaffRole(self.course_key), self.staff)
        self.assertEqual(
            'instructor',
            get_user_role(self.instructor, self.course_key)
        )

    def test_get_user_role_staff(self):
        """
        Verifies if user is staff.
        """
        add_users(self.global_admin, CourseStaffRole(self.course_key), self.staff)
        self.assertEqual(
            'staff',
            get_user_role(self.staff, self.course_key)
        )


class AllowJwtCookieAuthTest(CourseTestCase):
    """
    Tests for allow_jwt_cookie_auth on the Studio views that the authoring MFE calls.

    The clients here have no Studio session, like a user who signed in to the MFE
    through the LMS and never opened Studio directly.
    """

    def setUp(self):
        super().setUp()
        self.updates_url = reverse_course_url('course_info_update_handler', self.course.id)
        self.section = BlockFactory.create(parent=self.course, category='chapter', display_name='Section')
        subsection = BlockFactory.create(parent=self.section, category='sequential', display_name='Subsection')
        self.unit = BlockFactory.create(parent=subsection, category='vertical', display_name='Unit', publish_item=False)
        self.mfe_client, self.course_admin = self.create_non_staff_authed_user_client(authenticate=False)
        CourseInstructorRole(self.course.id).add_users(self.course_admin)

    @staticmethod
    def set_jwt_cookie(client, user):
        client.cookies[jwt_cookie_name()] = create_jwt_for_user(user)

    def test_no_session_and_no_jwt_cookie_redirects_to_login(self):
        response = self.mfe_client.get_json(self.updates_url)
        assert response.status_code == 302

    def test_invalid_jwt_cookie_redirects_to_login(self):
        self.mfe_client.cookies[jwt_cookie_name()] = 'not-a-jwt'
        response = self.mfe_client.get_json(self.updates_url)
        assert response.status_code == 302

    def test_jwt_cookie_course_admin_gets_course_updates(self):
        self.set_jwt_cookie(self.mfe_client, self.course_admin)
        response = self.mfe_client.get_json(self.updates_url)
        assert response.status_code == 200
        assert response.json() == []

    def test_jwt_cookie_user_without_course_access_is_denied(self):
        client = AjaxEnabledTestClient()
        self.set_jwt_cookie(client, UserFactory())
        response = client.get_json(self.updates_url)
        assert response.status_code == 403

    def test_jwt_cookie_course_admin_gets_outline(self):
        self.set_jwt_cookie(self.mfe_client, self.course_admin)
        response = self.mfe_client.get_json(reverse_usage_url('xblock_outline_handler', self.section.location))
        assert response.status_code == 200

    def test_jwt_cookie_course_admin_publishes_with_csrf_token(self):
        client = AjaxEnabledTestClient(enforce_csrf_checks=True)
        self.set_jwt_cookie(client, self.course_admin)
        # This GET sets the CSRF cookie (ensure_csrf_cookie).
        client.get_json(self.updates_url)
        csrf_token = client.cookies[settings.CSRF_COOKIE_NAME].value

        response = client.ajax_post(
            reverse_usage_url('xblock_handler', self.unit.location),
            {'publish': 'make_public'},
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        assert response.status_code == 200
        assert modulestore().has_published_version(modulestore().get_item(self.unit.location))

    def test_jwt_cookie_publish_without_csrf_token_is_rejected(self):
        client = AjaxEnabledTestClient(enforce_csrf_checks=True)
        self.set_jwt_cookie(client, self.course_admin)

        response = client.ajax_post(
            reverse_usage_url('xblock_handler', self.unit.location),
            {'publish': 'make_public'},
        )

        assert response.status_code == 403
        assert not modulestore().has_published_version(modulestore().get_item(self.unit.location))
