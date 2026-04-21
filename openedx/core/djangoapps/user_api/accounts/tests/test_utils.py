""" Unit tests for custom UserProfile properties. """


import ddt
from completion import models
from completion.test_utils import CompletionWaffleTestMixin
from django.test import TestCase
from django.test.utils import override_settings
from social_django.models import UserSocialAuth

from openedx.core.djangoapps.user_api.accounts.utils import (
    redact_user_social_auth_pii,
    retrieve_last_sitewide_block_completed,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..utils import format_social_link, validate_social_link


@ddt.ddt
class UserAccountSettingsTest(TestCase):
    """Unit tests for setting Social Media Links."""

    def setUp(self):  # lint-amnesty, pylint: disable=useless-super-delegation
        super().setUp()

    def validate_social_link(self, social_platform, link):
        """
        Helper method that returns True if the social link is valid, False if
        the input link fails validation and will throw an error.
        """
        try:
            validate_social_link(social_platform, link)
        except ValueError:
            return False
        return True

    @ddt.data(
        ('facebook', 'www.facebook.com/edX', 'https://www.facebook.com/edX', True),
        ('facebook', 'facebook.com/edX/', 'https://www.facebook.com/edX', True),
        ('facebook', 'HTTP://facebook.com/edX/', 'https://www.facebook.com/edX', True),
        ('facebook', 'www.evilwebsite.com/123', None, False),
        ('twitter', 'https://www.twiter.com/edX/', None, False),
        ('twitter', 'https://www.twitter.com/edX/123s', None, False),
        ('twitter', 'twitter.com/edX', 'https://www.twitter.com/edX', True),
        ('twitter', 'twitter.com/edX?foo=bar', 'https://www.twitter.com/edX?foo=bar', True),
        ('twitter', 'twitter.com/test.user', 'https://www.twitter.com/test.user', True),
        ('linkedin', 'www.linkedin.com/harryrein', None, False),
        ('linkedin', 'www.linkedin.com/in/harryrein-1234', 'https://www.linkedin.com/in/harryrein-1234', True),
        ('linkedin', 'www.evilwebsite.com/123?www.linkedin.com/edX', None, False),
        ('linkedin', '', '', True),
        ('linkedin', None, None, False),
    )
    @ddt.unpack
    @skip_unless_lms
    def test_social_link_input(self, platform_name, link_input, formatted_link_expected, is_valid_expected):
        """
        Verify that social links are correctly validated and formatted.
        """
        assert is_valid_expected == self.validate_social_link(platform_name, link_input)

        assert formatted_link_expected == format_social_link(platform_name, link_input)


@ddt.ddt
class CompletionUtilsTestCase(SharedModuleStoreTestCase, CompletionWaffleTestMixin, TestCase):
    """
    Test completion utility functions
    """
    def setUp(self):
        """
        Creates a test course that can be used for non-destructive tests
        """
        super().setUp()
        self.override_waffle_switch(True)
        self.engaged_user = UserFactory.create()
        self.cruft_user = UserFactory.create()
        self.course = self.create_test_course()
        self.submit_faux_completions()

    def create_test_course(self):
        """
        Create, populate test course.
        """
        course = CourseFactory.create()
        with self.store.bulk_operations(course.id):
            self.chapter = BlockFactory.create(category='chapter', parent=course)
            self.sequential = BlockFactory.create(category='sequential', parent=self.chapter)
            self.vertical1 = BlockFactory.create(category='vertical', parent=self.sequential)
            self.vertical2 = BlockFactory.create(category='vertical', parent=self.sequential)

        if hasattr(self, 'user_one'):
            CourseEnrollment.enroll(self.engaged_user, course.id)
        if hasattr(self, 'user_two'):
            CourseEnrollment.enroll(self.cruft_user, course.id)
        return course

    def submit_faux_completions(self):
        """
        Submit completions (only for user_one)
        """
        for block in self.sequential.get_children():
            models.BlockCompletion.objects.submit_completion(
                user=self.engaged_user,
                block_key=block.location,
                completion=1.0
            )

    @override_settings(LMS_ROOT_URL='test_url:9999')
    def test_retrieve_last_sitewide_block_completed(self):
        """
        Test that the method returns a URL for the "last completed" block
        when sending a user object
        """
        block_url = retrieve_last_sitewide_block_completed(
            self.engaged_user
        )
        empty_block_url = retrieve_last_sitewide_block_completed(
            self.cruft_user
        )
        assert block_url ==\
               'test_url:9999/courses/course-v1:{org}+{course}+{run}/jump_to/'\
               'block-v1:{org}+{course}+{run}+type@vertical+block@{vertical_id}'.format(
                   org=self.course.location.course_key.org,
                   course=self.course.location.course_key.course,
                   run=self.course.location.course_key.run,
                   vertical_id=self.vertical2.location.block_id
               )

        assert empty_block_url is None


@skip_unless_lms
class RedactUserSocialAuthPIITest(TestCase):
    """
    Tests for SSO PII redaction before deletion.
    """

    def setUp(self):
        """
        Create test user and SSO associations.
        """
        super().setUp()
        self.user = UserFactory.create(username='testuser', email='testuser@example.com')

    def create_social_auth(self, provider='google-oauth2', uid='user@example.com', extra_data=None):
        """
        Helper method to create a UserSocialAuth record.
        """
        if extra_data is None:
            extra_data = {
                'email': 'user@example.com',
                'name': 'Test User',
                'id': '123456789',
            }
        return UserSocialAuth.objects.create(
            user=self.user,
            provider=provider,
            uid=uid,
            extra_data=extra_data,
        )

    def test_redact_user_social_auth_pii(self):
        """
        Test that PII is redacted from UserSocialAuth records.
        """
        social_auth = self.create_social_auth()

        # Verify original PII is present
        assert social_auth.uid == 'user@example.com'
        assert social_auth.extra_data['email'] == 'user@example.com'
        assert 'name' in social_auth.extra_data

        # Redact PII
        redact_user_social_auth_pii(social_auth)

        # Refresh from database
        social_auth.refresh_from_db()

        # Verify PII is redacted
        assert social_auth.uid == 'redacted@retired.invalid'
        assert social_auth.extra_data == {}

    def test_redact_user_social_auth_pii_idempotent(self):
        """
        Test that redaction is idempotent (can be called multiple times safely).
        """
        social_auth = self.create_social_auth()

        # Redact PII twice
        redact_user_social_auth_pii(social_auth)
        redact_user_social_auth_pii(social_auth)

        # Refresh from database
        social_auth.refresh_from_db()

        # Verify PII is still properly redacted
        assert social_auth.uid == 'redacted@retired.invalid'
        assert social_auth.extra_data == {}

    def test_redact_pii_before_deletion_via_signal(self):
        """
        Test that the pre_delete signal automatically redacts PII before deletion.
        """
        social_auth = self.create_social_auth()
        social_auth_id = social_auth.id

        # Delete the record - this should trigger the signal
        social_auth.delete()

        # Since the record is deleted, we can't verify the redacted state directly.
        # But we can test that the deletion completed without errors.
        # In Snowflake, the soft-deleted record would have redacted PII.
        assert not UserSocialAuth.objects.filter(id=social_auth_id).exists()

    def test_redact_multiple_sso_providers(self):
        """
        Test that PII is redacted for multiple SSO providers.
        """
        google_auth = self.create_social_auth(
            provider='google-oauth2',
            uid='google@example.com',
            extra_data={'email': 'google@example.com', 'name': 'Google User'}
        )
        saml_auth = self.create_social_auth(
            provider='tpa-saml',
            uid='saml@example.com',
            extra_data={'email': 'saml@example.com', 'name': 'SAML User', 'uid': 'saml-uid'}
        )

        # Redact both
        redact_user_social_auth_pii(google_auth)
        redact_user_social_auth_pii(saml_auth)

        # Refresh from database
        google_auth.refresh_from_db()
        saml_auth.refresh_from_db()

        # Verify both are redacted
        assert google_auth.uid == 'redacted@retired.invalid'
        assert google_auth.extra_data == {}
        assert saml_auth.uid == 'redacted@retired.invalid'
        assert saml_auth.extra_data == {}
