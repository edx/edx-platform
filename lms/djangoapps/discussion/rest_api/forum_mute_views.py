"""
Updated Mute Views using Forum Service Integration.
These views replace the existing mute functionality to use the forum models and API.
"""

import logging
from urllib.parse import unquote

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import PermissionDenied
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser

from lms.djangoapps.discussion.rest_api.permissions import (
    CanMuteUsers
)
from lms.djangoapps.discussion.rest_api.serializers import (
    MuteRequestSerializer,
    UnmuteRequestSerializer,
    MuteAndReportRequestSerializer
)
from lms.djangoapps.discussion.django_comment_client.utils import has_discussion_privileges
from forum import api as forum_api
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin
from openedx.core.djangoapps.django_comment_common.comment_client.thread import Thread
from openedx.core.djangoapps.django_comment_common.comment_client.comment import Comment
from openedx.core.djangoapps.django_comment_common.comment_client.utils import CommentClientRequestError

log = logging.getLogger(__name__)
User = get_user_model()


def _get_target_user_and_data(request_data, course_id):
    """
    Extract target user and normalized mute data from request payload.

    Returns:
        (User, dict) on success
        (None, None) on failure
    """
    try:
        if 'username' in request_data:
            target_user = User.objects.get(
                username=request_data['username']
            )
            muted_user_id = target_user.id
            scope = (
                'course'
                if request_data.get('is_course_wide')
                else 'personal'
            )
            course_id_value = course_id
        else:
            muted_user_id = request_data.get('muted_user_id')
            target_user = User.objects.get(id=muted_user_id)
            scope = request_data.get('scope', 'personal')
            course_id_value = request_data.get('course_id', course_id)

        data = {
            'muted_user_id': muted_user_id,
            'course_id': course_id_value,
            'scope': scope,
            'reason': request_data.get('reason', ''),
            'muter_id': request_data.get('muter_id'),
        }

        return target_user, data

    except (User.DoesNotExist, ValueError, TypeError):
        return None, None


def _is_privileged_user(user, course_key):
    """Check if user has discussion moderation privileges."""
    return (
        has_discussion_privileges(user, course_key) or
        user.is_staff
    )


class ForumMuteUserView(DeveloperErrorViewMixin, APIView):
    """
    API endpoint to mute a user in discussions using forum service.

    **POST /api/discussion/v1/moderation/forum-mute/**

    Allows users to mute other users either personally or course-wide (if they have permissions).
    """
    authentication_classes = [
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    ]
    permission_classes = [CanMuteUsers]

    def post(self, request, course_id):
        """Mute a user in discussions using forum service"""
        course_id = unquote(course_id)
        target_user, data = _get_target_user_and_data(request.data, course_id)

        if not target_user:
            raise Http404("Target user not found")

        # Validate data
        serializer = MuteRequestSerializer(data=data)
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        course_key = CourseKey.from_string(course_id)

        # Check self-mute and permissions
        if request.user.id == target_user.id:
            raise ValidationError("Users cannot mute themselves")

        # For course-wide actions, user must have permissions to mute at course level
        if not CanMuteUsers.can_mute(request.user, target_user, course_key, data.get('scope', 'personal')):
            raise PermissionDenied("Permission denied")

        # Call forum API
        try:
            result = forum_api.mute_user(
                muted_user_id=str(target_user.id),
                muter_id=str(request.user.id),
                course_id=str(course_key),
                scope=data.get('scope', 'personal'),
                reason=data.get('reason', ''),
                requester_is_privileged=_is_privileged_user(request.user, course_key)
            )
            return Response(result, status=status.HTTP_201_CREATED)
        except Exception as e:  # pylint: disable=broad-exception-caught
            if "already muted" in str(e).lower():
                raise ValidationError("User is already muted") from e
            log.exception(f"Error muting user {target_user.id} in course {course_key}")
            raise ValidationError("Unable to mute user") from e


class ForumUnmuteUserView(DeveloperErrorViewMixin, APIView):
    """
    API endpoint to unmute a user in discussions using forum service.

    **POST /api/discussion/v1/moderation/forum-unmute/{course_id}/**

    Allows users to unmute previously muted users.
    """
    authentication_classes = [
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    ]
    permission_classes = [CanMuteUsers]

    def post(self, request, course_id):
        """Unmute a user in discussions using forum service"""
        course_id = unquote(course_id)
        target_user, data = _get_target_user_and_data(request.data, course_id)

        if not target_user:
            raise Http404("Target user not found")

        # Validate data
        serializer = UnmuteRequestSerializer(data=data)
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        course_key = CourseKey.from_string(course_id)
        scope = data.get('scope', 'personal')

        # Check permissions
        if not CanMuteUsers.can_unmute(request.user, target_user, course_key, scope):
            raise PermissionDenied("Permission denied")

        # Handle muter_id for personal unmutes
        muter_id = None
        if scope == 'personal':
            muter_id = request.user.id

        # Call forum API
        try:
            result = forum_api.unmute_user(
                muted_user_id=str(target_user.id),
                unmuted_by_id=str(request.user.id),
                course_id=str(course_key),
                scope=scope,
                muter_id=str(muter_id) if muter_id else None
            )
            return Response(result)
        except Exception as e:  # pylint: disable=broad-exception-caught
            if "no active mute found" in str(e).lower():
                raise Http404("No active mute found") from e
            log.exception(f"Error unmuting user {target_user.id} in course {course_key}")
            raise ValidationError("Unable to unmute user") from e


class ForumMuteAndReportView(DeveloperErrorViewMixin, APIView):
    """
    API endpoint to mute a user and report their content using forum service.

    **POST /api/discussion/v1/moderation/forum-mute-and-report/{course_id}/**
    """
    authentication_classes = [
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    ]
    permission_classes = [CanMuteUsers]

    def post(self, request, course_id):
        """Mute a user and report their content using forum service"""
        course_id = unquote(course_id)
        course_key = CourseKey.from_string(course_id)
        raw_data = request.data.copy()

        # Handle frontend format
        if 'username' in raw_data:
            try:
                target_user = User.objects.get(username=raw_data.get('username'))
            except User.DoesNotExist as exc:
                raise Http404("Target user not found") from exc

            # Handle post_id (thread or comment)
            thread_id = comment_id = ''
            post_id = raw_data.get('post_id', '')
            if post_id:
                try:
                    Thread.find(post_id).retrieve()
                    thread_id = post_id
                except (CommentClientRequestError, Exception):  # pylint: disable=broad-exception-caught
                    try:
                        Comment.find(post_id).retrieve()
                        comment_id = post_id
                    except (CommentClientRequestError, Exception):  # pylint: disable=broad-exception-caught
                        log.warning(f"Post ID {post_id} not found as thread or comment")

            data = {
                'muted_user_id': target_user.id,
                'course_id': course_id,
                'scope': 'course' if raw_data.get('is_course_wide') else 'personal',
                'reason': raw_data.get('reason', ''),
                'thread_id': thread_id,
                'comment_id': comment_id,
            }
        else:
            data = {
                'muted_user_id': raw_data.get('muted_user_id'),
                'course_id': raw_data.get('course_id', course_id),
                'scope': raw_data.get('scope', 'personal'),
                'reason': raw_data.get('reason', ''),
                'thread_id': raw_data.get('thread_id', ''),
                'comment_id': raw_data.get('comment_id', '')
            }
            try:
                target_user = get_object_or_404(User, id=data['muted_user_id'])
            except (User.DoesNotExist, ValueError, TypeError) as exc:
                raise Http404("Target user not found") from exc

        # Validate data
        serializer = MuteAndReportRequestSerializer(data=data)
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        # Check self-mute and permissions
        if request.user.id == target_user.id:
            raise ValidationError("Users cannot mute themselves")

        # For course-wide actions, user must have permissions to mute at course level
        if not CanMuteUsers.can_mute(request.user, target_user, course_key, data.get('scope', 'personal')):
            raise PermissionDenied("Permission denied")

        # Call forum API
        try:
            result = forum_api.mute_and_report_user(
                muted_user_id=str(target_user.id),
                muter_id=str(request.user.id),
                course_id=str(course_key),
                scope=data.get('scope', 'personal'),
                reason=data.get('reason', ''),
                thread_id=data.get('thread_id', ''),
                comment_id=data.get('comment_id', ''),
                request=request,
                requester_is_privileged=_is_privileged_user(request.user, course_key)
            )
            return Response(result, status=status.HTTP_201_CREATED)
        except Exception as e:  # pylint: disable=broad-exception-caught
            if "already muted" in str(e).lower():
                raise ValidationError("User is already muted") from e
            log.exception(f"Error muting and reporting user {target_user.id} in course {course_key}")
            raise ValidationError("Unable to mute and report user") from e


class ForumMutedUsersListView(DeveloperErrorViewMixin, APIView):
    """
    API endpoint to get the list of muted users using forum service.

    **GET /api/discussion/v1/moderation/forum-muted-users/{course_id}/**

    Query Parameters:
    - scope: Filter by mute scope ('personal', 'course', or 'all'). Default: 'all'
    - muted_by: Filter by user ID who performed the mute operation. Default: current user
      * Privacy restrictions: Non-staff users can only view their own mutes (muted_by is restricted to self)
      * Staff users can view any user's mutes by providing their user ID
      * For 'course' scope: muted_by is ignored as it returns all course-wide mutes regardless of who muted them
    - include_usernames: Include username resolution. Default: true
    """
    authentication_classes = [
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    ]
    permission_classes = [CanMuteUsers]

    def get(self, request, course_id):
        """Get list of muted users using forum service"""
        course_id = unquote(course_id)
        course_key = CourseKey.from_string(course_id)

        # Get parameters
        scope = request.query_params.get('scope', 'all')
        muted_by = request.query_params.get('muted_by')
        include_usernames = request.query_params.get('include_usernames', 'true').lower() == 'true'

        # Check staff permissions
        is_staff = _is_privileged_user(request.user, course_key)

        # Enforce restrictions for non-staff users
        if not is_staff:
            scope = 'personal'
            # Non-staff can only view their own mutes
            if muted_by and str(muted_by) != str(request.user.id):
                raise PermissionDenied("Non-staff users can only view their own mutes")
        else:
            # Staff can view other users' mutes, validate the muted_by user exists
            if muted_by and str(muted_by) != str(request.user.id):
                try:
                    User.objects.get(id=muted_by)
                except (User.DoesNotExist, ValueError, TypeError) as exc:
                    raise ValidationError({"muted_by": ["Invalid muted_by user ID"]}) from exc

        # Determine requester_id based on scope and muted_by parameter
        if scope == 'personal':
            requester_id = str(muted_by) if muted_by else str(request.user.id)
        elif scope == 'course':
            # muted_by is ignored for course scope as it gets all course mutes
            requester_id = None
        else:  # scope == 'all'
            requester_id = str(muted_by) if muted_by else str(request.user.id)

        # Call forum API
        try:
            result = forum_api.get_all_muted_users_for_course(
                course_id=str(course_key),
                requester_id=requester_id,
                scope=scope,
                requester_is_privileged=is_staff
            )

            # Process results if usernames needed
            muted_users = result.get('muted_users', [])
            if include_usernames and muted_users:
                user_ids = {int(user['muted_user_id']) for user in muted_users if user.get('muted_user_id')} | \
                    {int(user['muter_id']) for user in muted_users if user.get('muter_id')}
                users_bulk = User.objects.filter(id__in=user_ids).in_bulk()

                for user_data in muted_users:
                    # Add usernames
                    if user_data.get('muted_user_id'):
                        user_obj = users_bulk.get(int(user_data['muted_user_id']))
                        user_data['username'] = user_obj.username if user_obj else 'Unknown'
                    if user_data.get('muter_id'):
                        muter_obj = users_bulk.get(int(user_data['muter_id']))
                        user_data['muted_by_username'] = muter_obj.username if muter_obj else 'Unknown'

            # Separate by scope for frontend
            # Personal muted users should only include mutes made BY the current user
            personal_muted = [
                u for u in muted_users
                if u.get('scope') == 'personal' and str(u.get('muter_id')) == str(request.user.id)
            ]
            course_wide_muted = [u for u in muted_users if u.get('scope') == 'course']

            # Filter main muted_users list to exclude other users' personal mutes
            filtered_muted_users = [
                u for u in muted_users
                if u.get('scope') != 'personal' or str(u.get('muter_id')) == str(request.user.id)
            ]

            return Response({
                'status': 'success',
                'muted_users': filtered_muted_users,
                'personal_muted_users': personal_muted,
                'course_wide_muted_users': course_wide_muted,
                'total_count': len(filtered_muted_users),
                'personal_count': len(personal_muted),
                'course_wide_count': len(course_wide_muted),
                'requester_id': requester_id,
                'course_id': str(course_key),
                'scope_filter': scope,
            }, status=status.HTTP_200_OK)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            log.exception(f"Error getting muted users for course {course_id}")
            raise ValidationError("Unable to retrieve muted users") from exc


class ForumMuteStatusView(DeveloperErrorViewMixin, APIView):
    """
    API endpoint to get mute status for a user using forum service.

    **GET /api/discussion/v1/moderation/forum-mute-status/{course_id}/{user_id}/**
    """
    authentication_classes = [
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    ]
    permission_classes = [CanMuteUsers]

    def get(self, request, course_id, user_id):
        """Get mute status for a user using forum service"""
        course_id = unquote(course_id)

        # Validate user_id
        try:
            user_id = int(user_id)
        except (ValueError, TypeError) as exc:
            raise ValidationError({"user_id": ["Invalid user ID"]}) from exc

        # Call forum API
        try:
            result = forum_api.get_user_mute_status(
                user_id=str(user_id),
                course_id=str(CourseKey.from_string(course_id)),
                viewer_id=str(request.user.id)
            )
            return Response(result)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            log.exception(f"Error getting mute status for user {user_id}")
            raise ValidationError("Unable to retrieve mute status") from exc
