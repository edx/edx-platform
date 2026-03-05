"""
Tests for Discussion API internal interface
"""

from unittest import mock

import ddt
import pytest
from django.contrib.auth import get_user_model
from django.test.client import RequestFactory
from opaque_keys.edx.keys import CourseKey

from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from common.djangoapps.student.tests.factories import (
    UserFactory
)
from lms.djangoapps.discussion.rest_api.api import get_user_comments
from lms.djangoapps.discussion.rest_api.tests.utils import (
    ForumMockUtilsMixin,
    make_minimal_cs_comment,
)
from openedx.core.lib.exceptions import CourseNotFoundError, PageNotFoundError

User = get_user_model()


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
