"""
Track Selection API for Learning MFE.
"""

from django.http import Http404
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys.edx.keys import CourseKey
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.djangoapps.course_modes.track_selection_data import (
    TrackSelectionRedirect,
    get_track_selection_page_data,
)
from lms.djangoapps.course_home_api.toggles import course_home_mfe_track_selection_is_active
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser


class TrackSelectionView(RetrieveAPIView):
    """
    GET api/course_home/track_selection/{course_key}

    Backend-for-frontend payload for the track selection plugin slot in frontend-app-learning.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated,)

    def get(self, request, *args, **kwargs):
        course_key_string = kwargs.get('course_key_string')
        course_key = CourseKey.from_string(course_key_string)

        if not course_home_mfe_track_selection_is_active(course_key):
            raise Http404

        result = get_track_selection_page_data(request, course_key_string)
        if isinstance(result, TrackSelectionRedirect):
            redirect_url = result.url
            if not redirect_url.startswith(('http://', 'https://')):
                redirect_url = request.build_absolute_uri(redirect_url)
            return Response({'redirect_url': redirect_url})

        return Response(result)
