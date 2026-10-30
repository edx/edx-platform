"""
API endpoints for RV plugin configuration.
"""

from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from lms.djangoapps.learner_home.waffle import DISABLE_RV


class RVPluginStatusView(APIView):
    """
    API to return whether RV plugins are enabled.
    """

    authentication_classes = (
        JwtAuthentication,
        SessionAuthentication,
    )
    permission_classes = (AllowAny,)

    def get(self, request):
        """
        Return the global RV plugin enabled status.
        """
        rv_enabled = not DISABLE_RV.is_enabled()

        response = Response({
            "rvEnabled": rv_enabled,
        })
        response["Cache-Control"] = "max-age=60"

        return response
