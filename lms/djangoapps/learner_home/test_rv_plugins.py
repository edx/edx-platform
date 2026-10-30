"""
Tests for RV plugin status API.
"""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.test import APIClient

from lms.djangoapps.learner_home.rv_plugins import RVPluginStatusView
from xmodule.modulestore.tests.django_utils import UserFactory


class RVPluginStatusViewTests(TestCase):
    """
    Tests for the RV plugin status API.
    """

    def setUp(self):
        self.url = reverse("rv_plugins_status")
        self.client = APIClient()

    @patch("lms.djangoapps.learner_home.rv_plugins.DISABLE_RV")
    def test_rv_enabled_when_disable_flag_is_off(self, mock_flag):
        mock_flag.is_enabled.return_value = False

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"rvEnabled": True})

    @patch("lms.djangoapps.learner_home.rv_plugins.DISABLE_RV")
    def test_rv_disabled_when_disable_flag_is_on(self, mock_flag):
        mock_flag.is_enabled.return_value = True

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"rvEnabled": False})

    @patch("lms.djangoapps.learner_home.rv_plugins.DISABLE_RV")
    def test_anonymous_request_is_allowed(self, mock_flag):
        mock_flag.is_enabled.return_value = False

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_authentication_classes_are_configured(self):
        self.assertEqual(
            RVPluginStatusView.authentication_classes,
            (JwtAuthentication, SessionAuthentication),
        )

    @patch("lms.djangoapps.learner_home.rv_plugins.DISABLE_RV")
    def test_logged_in_user_can_access_status(self, mock_flag):
        mock_flag.is_enabled.return_value = False

        user = UserFactory()
        self.client.force_authenticate(user=user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"rvEnabled": True})

    @patch("lms.djangoapps.learner_home.rv_plugins.DISABLE_RV")
    def test_cache_control_header(self, mock_flag):
        mock_flag.is_enabled.return_value = False

        response = self.client.get(self.url)

        self.assertEqual(response["Cache-Control"], "max-age=60")
