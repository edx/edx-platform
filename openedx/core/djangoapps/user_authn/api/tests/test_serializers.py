"""
Tests for serializers for the MFE Context
"""

from django.test import TestCase

from openedx.core.djangoapps.user_authn.api.tests.data_mock import (
    MFE_CONTEXT_WITH_TPA_DATA,
    MFE_CONTEXT_WITHOUT_TPA_DATA,
    SERIALIZED_MFE_CONTEXT_WITH_TPA_DATA,
    SERIALIZED_MFE_CONTEXT_WITHOUT_TPA_DATA,
)
from openedx.core.djangoapps.user_authn.serializers import ContextDataSerializer, MFEContextSerializer


class TestMFEContextSerializer(TestCase):
    """
    High-level unit tests for MFEContextSerializer
    """

    def test_mfe_context_serializer(self):
        """
        Test MFEContextSerializer with mock data that serializes data correctly
        """

        output_data = MFEContextSerializer(
            MFE_CONTEXT_WITH_TPA_DATA
        ).data

        self.assertDictEqual(
            output_data,
            SERIALIZED_MFE_CONTEXT_WITH_TPA_DATA
        )

    def test_mfe_context_serializer_default_response(self):
        """
        Test MFEContextSerializer with default data
        """
        serialized_data = MFEContextSerializer(
            MFE_CONTEXT_WITHOUT_TPA_DATA
        ).data

        self.assertDictEqual(
            serialized_data,
            SERIALIZED_MFE_CONTEXT_WITHOUT_TPA_DATA
        )

    def test_context_data_serializer_includes_enterprise_branding_when_present(self):
        context_data = {
            'currentProvider': None,
            'platformName': 'Open edX',
            'providers': [],
            'secondaryProviders': [],
            'finishAuthUrl': None,
            'errorMessage': None,
            'registerFormSubmitButtonText': None,
            'autoSubmitRegForm': False,
            'syncLearnerProfileData': False,
            'countryCode': None,
            'welcomePageRedirectUrl': None,
            'pipeline_user_details': {
                'username': 'jdoe',
                'email': 'jdoe@example.com',
                'fullname': 'Jane Doe',
                'first_name': 'Jane',
                'last_name': 'Doe',
            },
            'enterpriseBranding': {
                'enterpriseName': 'Example Enterprise',
                'enterpriseLogoUrl': 'https://example.com/logo.png',
                'enterpriseBrandedWelcomeString': 'Welcome, Enterprise Learner',
                'enterpriseSlug': 'example-enterprise',
                'platformWelcomeString': 'Welcome to the Platform',
            },
        }

        serialized = ContextDataSerializer(context_data).data

        self.assertDictEqual(
            serialized['enterpriseBranding'],
            {
                'enterpriseName': 'Example Enterprise',
                'enterpriseLogoUrl': 'https://example.com/logo.png',
                'enterpriseBrandedWelcomeString': 'Welcome, Enterprise Learner',
                'enterpriseSlug': 'example-enterprise',
                'platformWelcomeString': 'Welcome to the Platform',
            }
        )

        self.assertDictEqual(
            serialized['pipelineUserDetails'],
            {
                'username': 'jdoe',
                'email': 'jdoe@example.com',
                'name': 'Jane Doe',
                'firstName': 'Jane',
                'lastName': 'Doe',
            }
        )

    def test_context_data_serializer_omits_enterprise_branding_when_absent(self):
        context_data = {
            'currentProvider': None,
            'platformName': 'Open edX',
            'providers': [],
            'secondaryProviders': [],
            'finishAuthUrl': None,
            'errorMessage': None,
            'registerFormSubmitButtonText': None,
            'autoSubmitRegForm': False,
            'syncLearnerProfileData': False,
            'countryCode': None,
            'welcomePageRedirectUrl': None,
            'pipeline_user_details': {
                'username': 'jdoe',
                'email': 'jdoe@example.com',
                'fullname': 'Jane Doe',
                'first_name': 'Jane',
                'last_name': 'Doe',
            },
        }

        serialized = ContextDataSerializer(context_data).data

        self.assertNotIn('enterpriseBranding', serialized)

