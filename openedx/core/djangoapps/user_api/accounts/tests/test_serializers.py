"""
Test cases to cover Accounts-related serializers of the User API application
"""


import logging

from django.test import TestCase
from django.test.client import RequestFactory
from social_django.models import UserSocialAuth
from testfixtures import LogCapture

from common.djangoapps.student.models import UserProfile
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth.tests.factories import SAMLProviderConfigFactory
from openedx.core.djangoapps.user_api.accounts.serializers import UserReadOnlySerializer

LOGGER_NAME = "openedx.core.djangoapps.user_api.accounts.serializers"


class UserReadOnlySerializerTest(TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    def setUp(self):
        super().setUp()
        request_factory = RequestFactory()
        self.request = request_factory.get('/api/user/v1/accounts/')
        self.user = UserFactory.build(username='test_user', email='test_user@test.com')
        self.user.save()
        self.config = {
            "default_visibility": "public",
            "public_fields": [
                'email', 'name', 'username'
            ],
        }

    def test_serializer_data(self):
        """
        Test serializer return data properly.
        """
        UserProfile.objects.create(user=self.user, name='test name')
        data = UserReadOnlySerializer(self.user, configuration=self.config, context={'request': self.request}).data
        assert data['username'] == self.user.username
        assert data['name'] == 'test name'
        assert data['email'] == self.user.email

    def test_user_no_profile(self):
        """
        Test serializer return data properly when user does not have profile.
        """
        with LogCapture(LOGGER_NAME, level=logging.DEBUG) as logger:
            data = UserReadOnlySerializer(self.user, configuration=self.config, context={'request': self.request}).data
            logger.check(
                (LOGGER_NAME, 'WARNING', 'user profile for the user [test_user] does not exist')
            )

        assert data['username'] == self.user.username
        assert data['name'] is None

    def test_email_change_disabled_present_in_self_view(self):
        """
        email_change_disabled=True is included when custom_fields is set (self/staff view).
        """
        saml_config = SAMLProviderConfigFactory(disable_email_editing=True)
        UserSocialAuth.objects.create(
            user=self.user,
            provider='tpa-saml',
            uid=f'{saml_config.slug}:remote-user-id',
        )
        UserProfile.objects.create(user=self.user, name='Test User')
        admin_fields = list(self.config['public_fields']) + ['email_change_disabled']
        data = UserReadOnlySerializer(
            self.user,
            configuration=self.config,
            custom_fields=admin_fields,
            context={'request': self.request},
        ).data
        assert data.get('email_change_disabled') is True

    def test_email_change_disabled_absent_in_public_view(self):
        """
        email_change_disabled is not included in public (non-custom_fields) views.
        """
        saml_config = SAMLProviderConfigFactory(disable_email_editing=True)
        UserSocialAuth.objects.create(
            user=self.user,
            provider='tpa-saml',
            uid=f'{saml_config.slug}:remote-user-id',
        )
        UserProfile.objects.create(user=self.user, name='Test User')
        data = UserReadOnlySerializer(
            self.user,
            configuration=self.config,
            context={'request': self.request},
        ).data
        assert 'email_change_disabled' not in data

    def test_email_change_disabled_absent_when_flag_false(self):
        """
        email_change_disabled is not included when disable_email_editing=False.
        """
        saml_config = SAMLProviderConfigFactory(disable_email_editing=False)
        UserSocialAuth.objects.create(
            user=self.user,
            provider='tpa-saml',
            uid=f'{saml_config.slug}:remote-user-id',
        )
        UserProfile.objects.create(user=self.user, name='Test User')
        admin_fields = list(self.config['public_fields'])
        data = UserReadOnlySerializer(
            self.user,
            configuration=self.config,
            custom_fields=admin_fields,
            context={'request': self.request},
        ).data
        assert 'email_change_disabled' not in data
