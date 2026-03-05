"""
Tests for Discussion API internal interface
"""

from unittest import mock

import ddt
import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.contrib.auth import get_user_model
from django.test.client import RequestFactory
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import CourseLocator

from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory
from xmodule.modulestore.django import modulestore

from common.djangoapps.student.tests.factories import (
    CourseEnrollmentFactory,
    UserFactory
)
from common.djangoapps.util.testing import UrlResetMixin

from lms.djangoapps.discussion.rest_api.api import (
    create_comment,
    create_thread,
    get_course,
    get_course_topics,
    get_user_comments,
)
from lms.djangoapps.discussion.rest_api.exceptions import (
    DiscussionDisabledError,
)
from rest_framework.exceptions import PermissionDenied
from lms.djangoapps.discussion.rest_api.tests.utils import (
    ForumMockUtilsMixin,
    make_minimal_cs_comment,
)
from openedx.core.djangoapps.course_groups.models import CourseUserGroupPartitionGroup
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_MODERATOR,
    FORUM_ROLE_STUDENT,
    Role
)
from openedx.core.djangoapps.django_comment_common.comment_client.utils import (
    CommentClient500Error,
    CommentClientRequestError,
)
from openedx.core.lib.exceptions import CourseNotFoundError, PageNotFoundError

User = get_user_model()


def _remove_discussion_tab(course, user_id):
    """
    Remove the discussion tab for the course.

    user_id is passed to the modulestore as the editor of the xblock.
    """
    course.tabs = [tab for tab in course.tabs if not tab.type == 'discussion']
    modulestore().update_item(course, user_id)


def _discussion_disabled_course_for(user):
    """
    Create and return a course with discussions disabled.

    The user passed in will be enrolled in the course.
    """
    course_with_disabled_forums = CourseFactory.create()
    CourseEnrollmentFactory.create(user=user, course_id=course_with_disabled_forums.id)
    _remove_discussion_tab(course_with_disabled_forums, user.id)

    return course_with_disabled_forums


def _assign_role_to_user(user, course_id, role):
    """
    Unset the blackout period for course discussions.

    Arguments:
            user: User to assign role to
            course_id: Course id of the course user will be assigned role in
            role: Role assigned to user for course
    """
    role = Role.objects.create(name=role, course_id=course_id)
    role.users.set([user])


@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
@override_settings(DISCUSSION_MODERATION_EDIT_REASON_CODES={"test-edit-reason": "Test Edit Reason"})
@override_settings(DISCUSSION_MODERATION_CLOSE_REASON_CODES={"test-close-reason": "Test Close Reason"})
@ddt.ddt
class GetCourseTest(UrlResetMixin, SharedModuleStoreTestCase):
    """Test for get_course"""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(org="x", course="y", run="z")

    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        self.request = RequestFactory().get("/dummy")
        self.request.user = self.user

    def test_nonexistent_course(self):
        with pytest.raises(CourseNotFoundError):
            get_course(self.request, CourseLocator.from_string("course-v1:non+existent+course"))

    def test_not_enrolled(self):
        unenrolled_user = UserFactory.create()
        self.request.user = unenrolled_user
        with pytest.raises(CourseNotFoundError):
            get_course(self.request, self.course.id)

    def test_discussions_disabled(self):
        with pytest.raises(DiscussionDisabledError):
            get_course(self.request, _discussion_disabled_course_for(self.user).id)

    def test_discussions_disabled_v2(self):
        data = get_course(self.request, _discussion_disabled_course_for(self.user).id, False)
        assert data['show_discussions'] is False

    def test_basic(self):
        assert get_course(self.request, self.course.id) == {
            'id': str(self.course.id),
            'is_posting_enabled': True,
            'is_user_banned': False,
            'blackouts': [],
            'thread_list_url': 'http://testserver/api/discussion/v1/threads/?course_id=course-v1%3Ax%2By%2Bz',
            'following_thread_list_url':
                'http://testserver/api/discussion/v1/threads/?course_id=course-v1%3Ax%2By%2Bz&following=True',
            'topics_url': 'http://testserver/api/discussion/v1/course_topics/course-v1:x+y+z',
            'allow_anonymous': True,
            'allow_anonymous_to_peers': False,
            'enable_in_context': True,
            'group_at_subsection': False,
            'provider': 'legacy',
            "has_bulk_delete_privileges": False,
            'has_moderation_privileges': False,
            "is_course_staff": False,
            "is_course_admin": False,
            'is_group_ta': False,
            'is_user_admin': False,
            'user_roles': {'Student'},
            'edit_reasons': [{'code': 'test-edit-reason', 'label': 'Test Edit Reason'}],
            'post_close_reasons': [{'code': 'test-close-reason', 'label': 'Test Close Reason'}],
            'show_discussions': True,
            'is_notify_all_learners_enabled': False,
            'captcha_settings': {
                'enabled': False,
                'site_key': None,
            },
            "is_email_verified": True,
            "only_verified_users_can_post": False,
            "content_creation_rate_limited": False,
            "enable_discussion_ban": False,
        }

    @ddt.data(
        FORUM_ROLE_ADMINISTRATOR,
        FORUM_ROLE_MODERATOR,
        FORUM_ROLE_COMMUNITY_TA,
    )
    def test_privileged_roles(self, role):
        """
        Test that the api returns the correct roles and privileges.
        """
        _assign_role_to_user(user=self.user, course_id=self.course.id, role=role)
        course_meta = get_course(self.request, self.course.id)
        assert course_meta["has_moderation_privileges"]
        assert course_meta["user_roles"] == {FORUM_ROLE_STUDENT} | {role}


@ddt.ddt
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class GetUserCommentsTest(ForumMockUtilsMixin, SharedModuleStoreTestCase):
    """
    Tests for get_user_comments.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        super().disposeForumMocks()

    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create()

        # create staff user so that we don't need to worry about
        # permissions here
        self.user = UserFactory.create(is_staff=True)
        self.register_get_user_response(self.user)

        self.request = RequestFactory().get(f'/api/discussion/v1/users/{self.user.username}/{self.course.id}')
        self.request.user = self.user

    def test_call_with_single_results_page(self):
        """
        Assert that a minimal call with valid inputs, and single result,
        returns the expected response structure.
        """
        self.register_get_comments_response(
            [make_minimal_cs_comment()],
            page=1,
            num_pages=1,
        )
        response = get_user_comments(
            request=self.request,
            author=self.user,
            course_key=self.course.id,
        )
        assert "results" in response.data
        assert "pagination" in response.data
        assert response.data["pagination"]["count"] == 1
        assert response.data["pagination"]["num_pages"] == 1
        assert response.data["pagination"]["next"] is None
        assert response.data["pagination"]["previous"] is None

    @ddt.data(1, 2, 3)
    def test_call_with_paginated_results(self, page):
        """
        Assert that paginated results return the correct pagination
        information at the pagination boundaries.
        """
        self.register_get_comments_response(
            [make_minimal_cs_comment() for _ in range(30)],
            page=page,
            num_pages=3,
        )
        response = get_user_comments(
            request=self.request,
            author=self.user,
            course_key=self.course.id,
            page=page,
        )
        assert "pagination" in response.data
        assert response.data["pagination"]["count"] == 30
        assert response.data["pagination"]["num_pages"] == 3

        if page in (1, 2):
            assert response.data["pagination"]["next"] is not None
            assert f"page={page+1}" in response.data["pagination"]["next"]
        if page in (2, 3):
            assert response.data["pagination"]["previous"] is not None
            assert f"page={page-1}" in response.data["pagination"]["previous"]
        if page == 1:
            assert response.data["pagination"]["previous"] is None
        if page == 3:
            assert response.data["pagination"]["next"] is None

    def test_call_with_invalid_page(self):
        """
        Assert that calls for pages that exceed the existing number of
        results pages raise PageNotFoundError.
        """
        self.register_get_comments_response([], page=2, num_pages=1)
        with pytest.raises(PageNotFoundError):
            get_user_comments(
                request=self.request,
                author=self.user,
                course_key=self.course.id,
                page=2,
            )

    def test_call_with_non_existent_course(self):
        """
        Assert that calls for comments in a course that doesn't exist
        result in a CourseNotFoundError error.
        """
        self.register_get_comments_response(
            [make_minimal_cs_comment()],
            page=1,
            num_pages=1,
        )
        with pytest.raises(CourseNotFoundError):
            get_user_comments(
                request=self.request,
                author=self.user,
                course_key=CourseKey.from_string("course-v1:x+y+z"),
                page=2,
            )


def test_create_thread_denies_banned_user():
    request = RequestFactory().post('/dummy')
    request.user = mock.Mock()

    with mock.patch(
        "lms.djangoapps.discussion.rest_api.api._get_course",
        return_value=mock.Mock(),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.get_context",
        return_value={},
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.discussion_open_for_user",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api._check_initializable_thread_fields",
        side_effect=ValidationError("downstream validation"),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.ENABLE_DISCUSSION_BAN.is_enabled",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.forum_api.is_user_banned",
        return_value=True,
        create=True,
    ):
        with pytest.raises(PermissionDenied, match="You are banned from posting"):
            create_thread(request, {"course_id": "course-v1:x+y+z"})


def test_create_comment_denies_banned_user():
    request = RequestFactory().post('/dummy')
    request.user = mock.Mock()
    course = mock.Mock()
    course.id = CourseKey.from_string("course-v1:x+y+z")

    with mock.patch(
        "lms.djangoapps.discussion.rest_api.api._get_thread_and_context",
        return_value=({"closed": False}, {"course": course}),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.discussion_open_for_user",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api._check_initializable_comment_fields",
        side_effect=ValidationError("downstream validation"),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.ENABLE_DISCUSSION_BAN.is_enabled",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.forum_api.is_user_banned",
        return_value=True,
        create=True,
    ):
        with pytest.raises(PermissionDenied, match="You are banned from posting"):
            create_comment(request, {"thread_id": "test_thread"})


def test_create_thread_ban_check_backend_error_fails_open():
    request = RequestFactory().post('/dummy')
    request.user = mock.Mock(id=123)

    with mock.patch(
        "lms.djangoapps.discussion.rest_api.api._get_course",
        return_value=mock.Mock(),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.get_context",
        return_value={},
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.discussion_open_for_user",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api._check_initializable_thread_fields",
        side_effect=ValidationError("downstream validation"),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.ENABLE_DISCUSSION_BAN.is_enabled",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.forum_api.is_user_banned",
        side_effect=CommentClientRequestError("temporary backend failure"),
        create=True,
    ), mock.patch("lms.djangoapps.discussion.rest_api.api.log.warning") as warning_log:
        with pytest.raises(ValidationError):
            create_thread(request, {"course_id": "course-v1:x+y+z"})

    warning_log.assert_called_once()


def test_create_comment_ban_check_backend_error_fails_open():
    request = RequestFactory().post('/dummy')
    request.user = mock.Mock(id=123)
    course = mock.Mock()
    course.id = CourseKey.from_string("course-v1:x+y+z")

    with mock.patch(
        "lms.djangoapps.discussion.rest_api.api._get_thread_and_context",
        return_value=({"closed": False}, {"course": course}),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.discussion_open_for_user",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api._check_initializable_comment_fields",
        side_effect=ValidationError("downstream validation"),
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.ENABLE_DISCUSSION_BAN.is_enabled",
        return_value=True,
    ), mock.patch(
        "lms.djangoapps.discussion.rest_api.api.forum_api.is_user_banned",
        side_effect=CommentClient500Error("temporary backend failure"),
        create=True,
    ), mock.patch("lms.djangoapps.discussion.rest_api.api.log.warning") as warning_log:
        with pytest.raises(ValidationError):
            create_comment(request, {"thread_id": "test_thread"})

    warning_log.assert_called_once()
