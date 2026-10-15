"""
Tests for user_api signal handlers.
"""
from unittest import mock

import ddt
from django.db import DatabaseError, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from edx_toggles.toggles.testutils import override_waffle_switch
from openedx_events.learning.data import CourseData, CourseEnrollmentData, UserData, UserPersonalData
from openedx_events.learning.signals import COURSE_ENROLLMENT_CREATED

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..handlers import EMAIL_OPTIN_KEY, create_default_email_optin_on_enrollment
from ..models import UserOrgTag
from ..toggles import ENABLE_DEFAULT_EMAIL_OPTIN_ON_ENROLLMENT


@skip_unless_lms
@ddt.ddt
@override_waffle_switch(ENABLE_DEFAULT_EMAIL_OPTIN_ON_ENROLLMENT, active=True)
class CreateDefaultEmailOptinOnEnrollmentTest(SharedModuleStoreTestCase):
    """
    Tests for the COURSE_ENROLLMENT_CREATED handler that records a default email-optin preference.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(org='TestX')
        cls.org = cls.course.id.org

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()

    def _get_optin(self, user, org=None):
        """
        Return the user's email-optin UserOrgTag for ``org`` (defaults to the course's org), or None.
        """
        return UserOrgTag.objects.filter(user=user, org=org or self.org, key=EMAIL_OPTIN_KEY).first()

    def _call_handler(self, user):
        """
        Call the handler directly with the same enrollment data that CourseEnrollment.enroll sends.
        """
        enrollment = CourseEnrollmentData(
            user=UserData(
                pii=UserPersonalData(
                    username=user.username,
                    email=user.email,
                    name=user.profile.name,
                ),
                id=user.id,
                is_active=user.is_active,
            ),
            course=CourseData(
                course_key=self.course.id,
            ),
            mode='audit',
            is_active=True,
            creation_date=timezone.now(),
        )
        create_default_email_optin_on_enrollment(sender=None, signal=COURSE_ENROLLMENT_CREATED, enrollment=enrollment)

    def test_new_enrollment_records_default_false(self):
        assert self._get_optin(self.user) is None

        CourseEnrollment.enroll(self.user, self.course.id)

        preference = self._get_optin(self.user)
        assert preference is not None
        assert preference.value == 'False'

    @override_waffle_switch(ENABLE_DEFAULT_EMAIL_OPTIN_ON_ENROLLMENT, active=False)
    def test_switch_disabled_records_nothing(self):
        CourseEnrollment.enroll(self.user, self.course.id)

        assert self._get_optin(self.user) is None

    @ddt.data('True', 'False')
    def test_existing_choice_is_not_overwritten(self, existing_value):
        existing = UserOrgTag.objects.create(user=self.user, org=self.org, key=EMAIL_OPTIN_KEY, value=existing_value)

        CourseEnrollment.enroll(self.user, self.course.id)

        preference = self._get_optin(self.user)
        assert preference.value == existing_value
        assert preference.modified == existing.modified
        assert UserOrgTag.objects.filter(user=self.user, key=EMAIL_OPTIN_KEY).count() == 1

    def test_reenrollment_keeps_existing_choice(self):
        CourseEnrollment.enroll(self.user, self.course.id)
        UserOrgTag.objects.filter(user=self.user, org=self.org, key=EMAIL_OPTIN_KEY).update(value='True')
        CourseEnrollment.unenroll(self.user, self.course.id)

        CourseEnrollment.enroll(self.user, self.course.id)

        assert self._get_optin(self.user).value == 'True'

    def test_preference_for_other_org_does_not_count(self):
        UserOrgTag.objects.create(user=self.user, org='OtherX', key=EMAIL_OPTIN_KEY, value='True')

        CourseEnrollment.enroll(self.user, self.course.id)

        assert self._get_optin(self.user).value == 'False'
        assert self._get_optin(self.user, org='OtherX').value == 'True'

    @mock.patch('openedx.core.djangoapps.user_api.handlers.log')
    @mock.patch.object(UserOrgTag.objects, 'get_or_create', side_effect=DatabaseError('boom'))
    def test_error_does_not_block_enrollment(self, mock_get_or_create, mock_log):
        enrollment = CourseEnrollment.enroll(self.user, self.course.id)

        assert mock_get_or_create.called
        assert enrollment.is_active
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        assert self._get_optin(self.user) is None
        mock_log.exception.assert_called_once()

    def test_query_count_is_constant_per_enrollment(self):
        def count_queries(num_users):
            users = [UserFactory.create() for _ in range(num_users)]
            with CaptureQueriesContext(connection) as context:
                for user in users:
                    self._call_handler(user)
            return len(context.captured_queries)

        # Warm up caches (e.g. the waffle switch) so they don't skew the first measurement.
        count_queries(1)

        single = count_queries(1)
        assert count_queries(5) == 5 * single
        assert UserOrgTag.objects.filter(key=EMAIL_OPTIN_KEY, value='False').count() == 7

    def test_existing_choice_issues_no_writes(self):
        UserOrgTag.objects.create(user=self.user, org=self.org, key=EMAIL_OPTIN_KEY, value='True')
        self._call_handler(UserFactory.create())  # warm up caches

        with CaptureQueriesContext(connection) as context:
            self._call_handler(self.user)

        writes = [
            query['sql'] for query in context.captured_queries
            if query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
        ]
        assert not writes
