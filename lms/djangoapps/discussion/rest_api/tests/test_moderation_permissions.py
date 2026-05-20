"""
Tests for discussion moderation permissions.
"""
from unittest.mock import Mock, patch

from rest_framework.test import APIRequestFactory

from common.djangoapps.student.roles import CourseStaffRole, CourseInstructorRole, GlobalStaff
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.discussion.rest_api.permissions import (
    IsAllowedToBulkDelete,
    can_take_action_on_spam,
)
from openedx.core.djangoapps.django_comment_common.models import Role
from xmodule.modulestore.tests.factories import CourseFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase


class CanTakeActionOnSpamTest(ModuleStoreTestCase):
    """Tests for can_take_action_on_spam permission helper function."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(org='TestX', number='CS101', run='2024')
        self.course_key = self.course.id

    def test_global_staff_has_permission(self):
        """Global staff should have permission."""
        user = UserFactory.create(is_staff=True)
        self.assertTrue(can_take_action_on_spam(user, self.course_key))

    def test_global_staff_role_has_permission(self):
        """Users with GlobalStaff role should have permission."""
        user = UserFactory.create()
        GlobalStaff().add_users(user)
        self.assertTrue(can_take_action_on_spam(user, self.course_key))

    def test_course_staff_no_permission(self):
        """Course staff should NOT have permission (authoring role only)."""
        user = UserFactory.create()
        CourseStaffRole(self.course_key).add_users(user)
        self.assertFalse(can_take_action_on_spam(user, self.course_key))

    def test_course_instructor_no_permission(self):
        """Course instructors should NOT have permission (authoring role only)."""
        user = UserFactory.create()
        CourseInstructorRole(self.course_key).add_users(user)
        self.assertFalse(can_take_action_on_spam(user, self.course_key))

    def test_forum_moderator_has_permission(self):
        """Forum moderators should have permission for their course."""
        user = UserFactory.create()
        role = Role.objects.create(name='Moderator', course_id=self.course_key)
        role.users.add(user)
        self.assertTrue(can_take_action_on_spam(user, self.course_key))

    def test_forum_administrator_has_permission(self):
        """Forum administrators should have permission for their course."""
        user = UserFactory.create()
        role = Role.objects.create(name='Administrator', course_id=self.course_key)
        role.users.add(user)
        self.assertTrue(can_take_action_on_spam(user, self.course_key))

    def test_regular_student_no_permission(self):
        """Regular students should not have permission."""
        user = UserFactory.create()
        self.assertFalse(can_take_action_on_spam(user, self.course_key))

    def test_community_ta_no_permission(self):
        """Community TAs should not have bulk delete permission."""
        user = UserFactory.create()
        role = Role.objects.create(name='Community TA', course_id=self.course_key)
        role.users.add(user)
        self.assertFalse(can_take_action_on_spam(user, self.course_key))

    def test_staff_different_course_no_permission(self):
        """Discussion moderators from a different course should not have permission."""
        other_course = CourseFactory.create(org='OtherX', number='CS201', run='2024')
        user = UserFactory.create()
        role = Role.objects.create(name='Moderator', course_id=other_course.id)
        role.users.add(user)
        self.assertFalse(can_take_action_on_spam(user, self.course_key))

    def test_accepts_string_course_id(self):
        """Function should accept string course_id and convert it."""
        user = UserFactory.create()
        role = Role.objects.create(name='Moderator', course_id=self.course_key)
        role.users.add(user)
        self.assertTrue(can_take_action_on_spam(user, str(self.course_key)))


class IsAllowedToBulkDeleteTest(ModuleStoreTestCase):
    """Tests for IsAllowedToBulkDelete permission class."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(org='TestX', number='CS101', run='2024')
        self.course_key = str(self.course.id)
        self.factory = APIRequestFactory()
        self.permission = IsAllowedToBulkDelete()

    def _create_view_with_kwargs(self, course_id=None):
        """Helper to create a mock view with kwargs."""
        view = Mock()
        view.kwargs = {'course_id': course_id} if course_id else {}
        return view

    def _create_request_with_data(self, user, course_id=None, method='POST'):
        """Helper to create a request with data."""
        if method == 'POST':
            request = self.factory.post('/api/discussion/v1/moderation/bulk-delete-ban/')
        else:
            request = self.factory.get('/api/discussion/v1/moderation/banned-users/')

        request.user = user
        request.data = {'course_id': course_id} if course_id else {}
        return request

    def test_unauthenticated_user_denied(self):
        """Unauthenticated users should be denied."""
        request = self.factory.post('/api/discussion/v1/moderation/bulk-delete-ban/')
        request.user = Mock(is_authenticated=False)
        view = self._create_view_with_kwargs()

        self.assertFalse(self.permission.has_permission(request, view))

    def test_global_staff_with_course_id_in_data(self):
        """Global staff should have permission when course_id is in request data."""
        user = UserFactory.create(is_staff=True)
        request = self._create_request_with_data(user, self.course_key)
        view = self._create_view_with_kwargs()

        self.assertTrue(self.permission.has_permission(request, view))

    def test_course_staff_denied(self):
        """Course staff should NOT have permission (authoring role only)."""
        user = UserFactory.create()
        CourseStaffRole(self.course.id).add_users(user)
        request = self._create_request_with_data(user, self.course_key)
        view = self._create_view_with_kwargs()

        self.assertFalse(self.permission.has_permission(request, view))

    def test_course_instructor_denied(self):
        """Course instructors should NOT have permission (authoring role only)."""
        user = UserFactory.create()
        CourseInstructorRole(self.course.id).add_users(user)
        request = self._create_request_with_data(user, self.course_key)
        view = self._create_view_with_kwargs()

        self.assertFalse(self.permission.has_permission(request, view))

    def test_forum_moderator_with_course_id_in_data(self):
        """Forum moderators should have permission when course_id is in request data."""
        user = UserFactory.create()
        role = Role.objects.create(name='Moderator', course_id=self.course.id)
        role.users.add(user)
        request = self._create_request_with_data(user, self.course_key)
        view = self._create_view_with_kwargs()

        self.assertTrue(self.permission.has_permission(request, view))

    def test_regular_student_denied(self):
        """Regular students should be denied."""
        user = UserFactory.create()
        request = self._create_request_with_data(user, self.course_key)
        view = self._create_view_with_kwargs()

        self.assertFalse(self.permission.has_permission(request, view))

    def test_course_id_in_url_kwargs(self):
        """Permission should work when course_id is in URL kwargs."""
        user = UserFactory.create()
        role = Role.objects.create(name='Moderator', course_id=self.course.id)
        role.users.add(user)
        request = self.factory.get('/api/discussion/v1/moderation/banned-users/')
        request.user = user
        request.data = {}
        request.query_params = {}
        view = self._create_view_with_kwargs(self.course_key)

        self.assertTrue(self.permission.has_permission(request, view))

    def test_no_course_id_only_global_staff_allowed(self):
        """When no course_id provided, only global staff should be allowed."""
        # Global staff allowed
        global_staff = UserFactory.create(is_staff=True)
        request = self._create_request_with_data(global_staff)
        view = self._create_view_with_kwargs()
        self.assertTrue(self.permission.has_permission(request, view))

        # Regular user denied
        regular_user = UserFactory.create()
        request = self._create_request_with_data(regular_user)
        view = self._create_view_with_kwargs()
        self.assertFalse(self.permission.has_permission(request, view))

    def test_staff_different_course_denied(self):
        """Discussion moderators from different course should be denied."""
        other_course = CourseFactory.create(org='OtherX', number='CS201', run='2024')
        user = UserFactory.create()
        role = Role.objects.create(name='Moderator', course_id=other_course.id)
        role.users.add(user)
        request = self._create_request_with_data(user, self.course_key)
        view = self._create_view_with_kwargs()

        self.assertFalse(self.permission.has_permission(request, view))


class RoleBasedBanPermissionsTest(ModuleStoreTestCase):
    """Tests for role-based ban/unban permissions in DiscussionModerationViewSet."""

    # pylint: disable=protected-access

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(org='TestX', number='CS101', run='2024')
        self.course_key = self.course.id

        # Create users with different roles
        self.global_staff_user = UserFactory.create(username='global_staff')
        GlobalStaff().add_users(self.global_staff_user)

        self.admin_user = UserFactory.create(username='admin_user')
        admin_role = Role.objects.create(name='Administrator', course_id=self.course_key)
        admin_role.users.add(self.admin_user)

        self.moderator_user = UserFactory.create(username='moderator_user')
        moderator_role = Role.objects.create(name='Moderator', course_id=self.course_key)
        moderator_role.users.add(self.moderator_user)

        self.global_staff_admin_user = UserFactory.create(username='global_staff_admin')
        GlobalStaff().add_users(self.global_staff_admin_user)
        admin_role.users.add(self.global_staff_admin_user)

        self.global_staff_moderator_user = UserFactory.create(username='global_staff_moderator')
        GlobalStaff().add_users(self.global_staff_moderator_user)
        moderator_role.users.add(self.global_staff_moderator_user)

        self.regular_user = UserFactory.create(username='regular_user')

    @patch('lms.djangoapps.discussion.toggles.ENABLE_DISCUSSION_BAN.is_enabled')
    def test_global_staff_without_discussion_role_cannot_ban_moderator(self, mock_flag):
        """Global Staff without discussion role cannot ban Discussion Moderator."""
        from lms.djangoapps.discussion.rest_api.views import DiscussionModerationViewSet
        from rest_framework.response import Response

        mock_flag.return_value = True
        viewset = DiscussionModerationViewSet()
        request = Mock()
        request.user = self.global_staff_user

        result = viewset._validate_ban_request_and_get_user(
            request,
            {
                'lookup_username': self.moderator_user.username,
                'course_id': str(self.course_key),
                'scope': 'course',
            },
            check_privileged=False
        )

        # Should return error response
        self.assertIsInstance(result, Response)
        self.assertEqual(result.status_code, 403)
        self.assertIn('Global Staff cannot ban discussion privileged users', result.data['error'])

    @patch('lms.djangoapps.discussion.toggles.ENABLE_DISCUSSION_BAN.is_enabled')
    def test_global_staff_without_discussion_role_cannot_ban_admin(self, mock_flag):
        """Global Staff without discussion role cannot ban Discussion Admin."""
        from lms.djangoapps.discussion.rest_api.views import DiscussionModerationViewSet
        from rest_framework.response import Response

        mock_flag.return_value = True
        viewset = DiscussionModerationViewSet()
        request = Mock()
        request.user = self.global_staff_user

        result = viewset._validate_ban_request_and_get_user(
            request,
            {
                'lookup_username': self.admin_user.username,
                'course_id': str(self.course_key),
                'scope': 'course',
            },
            check_privileged=False
        )

        # Should return error response
        self.assertIsInstance(result, Response)
        self.assertEqual(result.status_code, 403)
        self.assertIn('Global Staff cannot ban discussion privileged users', result.data['error'])

    @patch('lms.djangoapps.discussion.toggles.ENABLE_DISCUSSION_BAN.is_enabled')
    def test_moderator_cannot_ban_admin(self, mock_flag):
        """Discussion Moderator should NOT be able to ban Discussion Admin."""
        from lms.djangoapps.discussion.rest_api.views import DiscussionModerationViewSet
        from rest_framework.response import Response

        mock_flag.return_value = True
        viewset = DiscussionModerationViewSet()
        request = Mock()
        request.user = self.moderator_user

        result = viewset._validate_ban_request_and_get_user(
            request,
            {
                'lookup_username': self.admin_user.username,
                'course_id': str(self.course_key),
                'scope': 'course',
            },
            check_privileged=False
        )

        # Should return error response
        self.assertIsInstance(result, Response)
        self.assertEqual(result.status_code, 403)
        self.assertIn('Discussion Moderators cannot ban Discussion Admins', result.data['error'])

    @patch('lms.djangoapps.discussion.toggles.ENABLE_DISCUSSION_BAN.is_enabled')
    def test_moderator_can_ban_global_staff(self, mock_flag):
        """Discussion Moderator should be able to ban Global Staff users."""
        from lms.djangoapps.discussion.rest_api.views import DiscussionModerationViewSet

        mock_flag.return_value = True
        viewset = DiscussionModerationViewSet()
        request = Mock()
        request.user = self.moderator_user

        result = viewset._validate_ban_request_and_get_user(
            request,
            {
                'lookup_username': self.global_staff_user.username,
                'course_id': str(self.course_key),
                'scope': 'course',
            },
            check_privileged=False
        )

        # Should return tuple (user, course_key, ban_scope, reason)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0], self.global_staff_user)

    @patch('lms.djangoapps.discussion.toggles.ENABLE_DISCUSSION_BAN.is_enabled')
    def test_admin_can_ban_moderator(self, mock_flag):
        """Discussion Admin should be able to ban Discussion Moderator."""
        from lms.djangoapps.discussion.rest_api.views import DiscussionModerationViewSet

        mock_flag.return_value = True
        viewset = DiscussionModerationViewSet()
        request = Mock()
        request.user = self.admin_user

        result = viewset._validate_ban_request_and_get_user(
            request,
            {
                'lookup_username': self.moderator_user.username,
                'course_id': str(self.course_key),
                'scope': 'course',
            },
            check_privileged=False
        )

        # Should return tuple
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], self.moderator_user)

    @patch('lms.djangoapps.discussion.toggles.ENABLE_DISCUSSION_BAN.is_enabled')
    def test_admin_can_ban_global_staff(self, mock_flag):
        """Discussion Admin should be able to ban Global Staff users."""
        from lms.djangoapps.discussion.rest_api.views import DiscussionModerationViewSet

        mock_flag.return_value = True
        viewset = DiscussionModerationViewSet()
        request = Mock()
        request.user = self.admin_user

        result = viewset._validate_ban_request_and_get_user(
            request,
            {
                'lookup_username': self.global_staff_user.username,
                'course_id': str(self.course_key),
                'scope': 'course',
            },
            check_privileged=False
        )

        # Should return tuple
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], self.global_staff_user)

    @patch('lms.djangoapps.discussion.toggles.ENABLE_DISCUSSION_BAN.is_enabled')
    def test_global_staff_without_discussion_role_cannot_ban_global_staff(self, mock_flag):
        """Global Staff without Discussion role cannot ban another Global Staff."""
        from lms.djangoapps.discussion.rest_api.views import DiscussionModerationViewSet
        from rest_framework.response import Response

        mock_flag.return_value = True
        viewset = DiscussionModerationViewSet()
        request = Mock()
        request.user = self.global_staff_user

        # Create another global staff user
        another_global_staff = UserFactory.create(username='another_global_staff')
        GlobalStaff().add_users(another_global_staff)

        result = viewset._validate_ban_request_and_get_user(
            request,
            {
                'lookup_username': another_global_staff.username,
                'course_id': str(self.course_key),
                'scope': 'course',
            },
            check_privileged=False
        )

        # Should return error response
        self.assertIsInstance(result, Response)
        self.assertEqual(result.status_code, 403)
        self.assertIn('Global Staff cannot ban another Global Staff', result.data['error'])

    @patch('lms.djangoapps.discussion.toggles.ENABLE_DISCUSSION_BAN.is_enabled')
    def test_global_staff_with_admin_role_can_ban_moderator(self, mock_flag):
        """Global Staff with Discussion Admin role can ban Discussion Moderators."""
        from lms.djangoapps.discussion.rest_api.views import DiscussionModerationViewSet

        mock_flag.return_value = True
        viewset = DiscussionModerationViewSet()
        request = Mock()
        request.user = self.global_staff_admin_user

        result = viewset._validate_ban_request_and_get_user(
            request,
            {
                'lookup_username': self.moderator_user.username,
                'course_id': str(self.course_key),
                'scope': 'course',
            },
            check_privileged=False
        )

        # Should return tuple
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], self.moderator_user)

    @patch('lms.djangoapps.discussion.toggles.ENABLE_DISCUSSION_BAN.is_enabled')
    def test_global_staff_with_admin_role_cannot_ban_admin(self, mock_flag):
        """Global Staff with Admin role cannot ban other Discussion Admins (Rule 2)."""
        from lms.djangoapps.discussion.rest_api.views import DiscussionModerationViewSet
        from rest_framework.response import Response

        mock_flag.return_value = True
        viewset = DiscussionModerationViewSet()
        request = Mock()
        request.user = self.global_staff_admin_user

        result = viewset._validate_ban_request_and_get_user(
            request,
            {
                'lookup_username': self.admin_user.username,
                'course_id': str(self.course_key),
                'scope': 'course',
            },
            check_privileged=False
        )

        # Should return error response - Rule 2 (Admins cannot ban other Admins)
        self.assertIsInstance(result, Response)
        self.assertEqual(result.status_code, 403)
        self.assertIn('Discussion Admins cannot ban other Discussion Admins', result.data['error'])

    @patch('lms.djangoapps.discussion.toggles.ENABLE_DISCUSSION_BAN.is_enabled')
    def test_global_staff_with_admin_role_cannot_ban_global_staff_without_moderator_role(self, mock_flag):
        """Global Staff with Admin role (but not Moderator) cannot ban Global Staff without Moderator role."""
        from lms.djangoapps.discussion.rest_api.views import DiscussionModerationViewSet
        from rest_framework.response import Response

        mock_flag.return_value = True
        viewset = DiscussionModerationViewSet()
        request = Mock()
        request.user = self.global_staff_admin_user

        result = viewset._validate_ban_request_and_get_user(
            request,
            {
                'lookup_username': self.global_staff_user.username,
                'course_id': str(self.course_key),
                'scope': 'course',
            },
            check_privileged=False
        )

        # Should return error response - Rule 4 (Global Staff+Admin can only ban Moderators)
        self.assertIsInstance(result, Response)
        self.assertEqual(result.status_code, 403)
        self.assertIn(
            'Global Staff with Discussion Admin role can only ban Discussion Moderators',
            result.data['error']
        )

    @patch('lms.djangoapps.discussion.toggles.ENABLE_DISCUSSION_BAN.is_enabled')
    def test_global_staff_with_moderator_role_can_ban_global_staff(self, mock_flag):
        """Global Staff with Moderator role can ban other Global Staff."""
        from lms.djangoapps.discussion.rest_api.views import DiscussionModerationViewSet

        mock_flag.return_value = True
        viewset = DiscussionModerationViewSet()
        request = Mock()
        request.user = self.global_staff_moderator_user

        result = viewset._validate_ban_request_and_get_user(
            request,
            {
                'lookup_username': self.global_staff_user.username,
                'course_id': str(self.course_key),
                'scope': 'course',
            },
            check_privileged=False
        )

        # Should return tuple
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], self.global_staff_user)

    @patch('lms.djangoapps.discussion.toggles.ENABLE_DISCUSSION_BAN.is_enabled')
    def test_moderator_can_ban_regular_user(self, mock_flag):
        """Discussion Moderator can ban regular users."""
        from lms.djangoapps.discussion.rest_api.views import DiscussionModerationViewSet

        mock_flag.return_value = True
        viewset = DiscussionModerationViewSet()
        request = Mock()
        request.user = self.moderator_user

        result = viewset._validate_ban_request_and_get_user(
            request,
            {
                'lookup_username': self.regular_user.username,
                'course_id': str(self.course_key),
                'scope': 'course',
            },
            check_privileged=False
        )

        # Should return tuple
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], self.regular_user)

    @patch('lms.djangoapps.discussion.toggles.ENABLE_DISCUSSION_BAN.is_enabled')
    def test_admin_can_ban_regular_user(self, mock_flag):
        """Discussion Admin can ban regular users."""
        from lms.djangoapps.discussion.rest_api.views import DiscussionModerationViewSet

        mock_flag.return_value = True
        viewset = DiscussionModerationViewSet()
        request = Mock()
        request.user = self.admin_user

        result = viewset._validate_ban_request_and_get_user(
            request,
            {
                'lookup_username': self.regular_user.username,
                'course_id': str(self.course_key),
                'scope': 'course',
            },
            check_privileged=False
        )

        # Should return tuple
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], self.regular_user)

    @patch('lms.djangoapps.discussion.toggles.ENABLE_DISCUSSION_BAN.is_enabled')
    def test_global_staff_with_admin_role_can_ban_regular_user(self, mock_flag):
        """Global Staff with Admin role can ban regular users."""
        from lms.djangoapps.discussion.rest_api.views import DiscussionModerationViewSet

        mock_flag.return_value = True
        viewset = DiscussionModerationViewSet()
        request = Mock()
        request.user = self.global_staff_admin_user

        result = viewset._validate_ban_request_and_get_user(
            request,
            {
                'lookup_username': self.regular_user.username,
                'course_id': str(self.course_key),
                'scope': 'course',
            },
            check_privileged=False
        )

        # Should return tuple (user, course_key, ban_scope, reason)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0], self.regular_user)
