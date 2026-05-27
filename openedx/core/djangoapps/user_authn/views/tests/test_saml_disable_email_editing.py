"""
Tests for the SAMLProviderConfig.disable_email_editing option.
"""

from unittest import mock

from django.test import TestCase, override_settings
from django.test.client import RequestFactory

from common.djangoapps.third_party_auth.tests.factories import SAMLProviderConfigFactory
from common.djangoapps.third_party_auth.tests.testutil import simulate_running_pipeline
from openedx.core.djangoapps.user_authn.views.registration_form import RegistrationFormFactory


class SAMLDisableEmailEditingTest(TestCase):
    """
    Tests that disable_email_editing=True makes the registration form email field
    read-only and that disable_email_editing=False leaves it editable.
    """

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()

    def _create_request(self):
        """Create a test request with session support."""
        from importlib import import_module
        from django.conf import settings
        request = self.factory.get('/register')
        engine = import_module(settings.SESSION_ENGINE)
        request.session = engine.SessionStore(None)
        return request

    def _get_email_field(self, form_desc):
        """Return the email field dict from a form description, or None."""
        return next((f for f in form_desc.fields if f['name'] == 'email'), None)

    @override_settings(REGISTRATION_EXTRA_FIELDS={}, REGISTRATION_FIELD_ORDER=[])
    @mock.patch(
        'openedx.core.djangoapps.user_authn.views.registration_form.third_party_auth.is_enabled',
        return_value=True,
    )
    def test_email_readonly_when_disable_email_editing_true(self, mock_is_enabled):
        """Email field has restrictions.readonly when disable_email_editing=True."""
        saml_config = SAMLProviderConfigFactory(disable_email_editing=True)

        with simulate_running_pipeline(
            "common.djangoapps.third_party_auth.pipeline",
            "tpa-saml",
            response={"idp_name": saml_config.slug},
            email="testuser@example.com",
            fullname="Test User",
            username="testuser",
        ):
            form_desc = RegistrationFormFactory().get_registration_form(self._create_request())

        email_field = self._get_email_field(form_desc)
        self.assertIsNotNone(email_field)
        restrictions = email_field.get('restrictions', {})
        self.assertEqual(
            restrictions.get('readonly'),
            'readonly',
            "Email field should have restrictions.readonly='readonly' when disable_email_editing=True",
        )
        self.assertIn('min_length', restrictions, "min_length restriction should be preserved alongside readonly")
        self.assertIn('max_length', restrictions, "max_length restriction should be preserved alongside readonly")

    @override_settings(REGISTRATION_EXTRA_FIELDS={}, REGISTRATION_FIELD_ORDER=[])
    @mock.patch(
        'openedx.core.djangoapps.user_authn.views.registration_form.third_party_auth.is_enabled',
        return_value=True,
    )
    def test_email_editable_when_disable_email_editing_true_but_no_email(self, mock_is_enabled):
        """Email field is not readonly when disable_email_editing=True but the pipeline provides no email."""
        saml_config = SAMLProviderConfigFactory(disable_email_editing=True)

        with simulate_running_pipeline(
            "common.djangoapps.third_party_auth.pipeline",
            "tpa-saml",
            response={"idp_name": saml_config.slug},
            fullname="Test User",
            username="testuser",
        ):
            form_desc = RegistrationFormFactory().get_registration_form(self._create_request())

        email_field = self._get_email_field(form_desc)
        self.assertIsNotNone(email_field)
        self.assertNotIn(
            'readonly',
            email_field.get('restrictions', {}),
            "Email field must be editable when the pipeline provides no email address",
        )

    @override_settings(REGISTRATION_EXTRA_FIELDS={}, REGISTRATION_FIELD_ORDER=[])
    @mock.patch(
        'openedx.core.djangoapps.user_authn.views.registration_form.third_party_auth.is_enabled',
        return_value=True,
    )
    def test_email_editable_when_disable_email_editing_false(self, mock_is_enabled):
        """Email field has no readonly restriction when disable_email_editing=False."""
        saml_config = SAMLProviderConfigFactory(disable_email_editing=False)

        with simulate_running_pipeline(
            "common.djangoapps.third_party_auth.pipeline",
            "tpa-saml",
            response={"idp_name": saml_config.slug},
            email="testuser@example.com",
            fullname="Test User",
            username="testuser",
        ):
            form_desc = RegistrationFormFactory().get_registration_form(self._create_request())

        email_field = self._get_email_field(form_desc)
        self.assertIsNotNone(email_field)
        self.assertNotIn(
            'readonly',
            email_field.get('restrictions', {}),
            "Email field should not have readonly restriction when disable_email_editing=False",
        )
