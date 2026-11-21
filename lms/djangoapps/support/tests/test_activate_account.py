"""
Tests for the support tool that confirms a learner's account.
"""

from unittest import mock
from uuid import uuid4

import ddt
from django.test import TestCase
from django.urls import reverse

from common.djangoapps.student.models import Registration
from common.djangoapps.student.roles import SupportStaffRole
from common.djangoapps.student.tests.factories import RegistrationFactory, UserFactory
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.signals.signals import USER_ACCOUNT_ACTIVATED

PASSWORD = 'test-password'


@ddt.ddt
class ActivateAccountViewTests(TestCase):
    """Tests for ActivateAccountView."""

    def setUp(self):
        super().setUp()
        self.learner = UserFactory(
            username='unconfirmed_learner', email='unconfirmed@example.com', password=PASSWORD, is_active=False
        )
        self.registration = RegistrationFactory(user=self.learner, activation_key=uuid4().hex)
        self.global_staff = UserFactory(is_staff=True, password=PASSWORD)
        self.support_staff = UserFactory(password=PASSWORD)
        SupportStaffRole().add_users(self.support_staff)

    def _url(self, username_or_email):
        return reverse('support:activate_account', kwargs={'username_or_email': username_or_email})

    def _login(self, caller):
        assert self.client.login(username=caller.username, password=PASSWORD), 'Could not log in'

    def _post(self, caller, username_or_email):
        self._login(caller)
        return self.client.post(self._url(username_or_email))

    def _refresh(self):
        self.learner.refresh_from_db()
        self.registration.refresh_from_db()

    @ddt.data('global_staff', 'support_staff')
    def test_support_can_activate(self, caller_attr):
        receiver = mock.Mock()
        USER_ACCOUNT_ACTIVATED.connect(receiver)
        self.addCleanup(USER_ACCOUNT_ACTIVATED.disconnect, receiver)
        caller = getattr(self, caller_attr)

        with self.assertLogs('lms.djangoapps.support.views.activate_account', level='INFO') as logs:
            response = self._post(caller, self.learner.username)

        assert response.status_code == 200
        assert response.json() == {'is_active': True, 'already_active': False}
        self._refresh()
        assert self.learner.is_active
        assert self.registration.activation_timestamp is not None
        # Same effects as the email link: Registration.activate() sends the activation signal.
        receiver.assert_called_once()
        assert receiver.call_args.kwargs['user'] == self.learner
        # The audit log names who confirmed whom, by id only.
        assert f'user {caller.id} confirmed the account of user {self.learner.id}' in logs.output[0]

    def test_lookup_by_email(self):
        response = self._post(self.support_staff, self.learner.email)
        assert response.status_code == 200
        self._refresh()
        assert self.learner.is_active

    def test_never_returns_the_activation_key(self):
        response = self._post(self.support_staff, self.learner.username)
        assert self.registration.activation_key not in response.content.decode()

    def test_already_active(self):
        active = UserFactory(is_active=True)
        RegistrationFactory(user=active, activation_key=uuid4().hex)
        response = self._post(self.support_staff, active.username)
        assert response.status_code == 200
        assert response.json() == {'is_active': True, 'already_active': True}

    def test_unknown_user(self):
        response = self._post(self.support_staff, 'nobody_by_that_name')
        assert response.status_code == 404
        assert response.json() == {'error': 'user_not_found'}

    def test_no_registration_record(self):
        no_registration = UserFactory(is_active=False)
        Registration.objects.filter(user=no_registration).delete()
        response = self._post(self.support_staff, no_registration.username)
        assert response.status_code == 404
        assert response.json() == {'error': 'registration_not_found'}
        no_registration.refresh_from_db()
        assert not no_registration.is_active

    def test_regular_user_is_forbidden(self):
        response = self._post(UserFactory(password=PASSWORD), self.learner.username)
        assert response.status_code == 403
        self._refresh()
        assert not self.learner.is_active

    def test_owner_cannot_activate_themselves(self):
        """
        The attack the 2026-04 security fix closed: an unconfirmed user can't log in with a password, but can get an
        OAuth token. Even with their own valid token they must not be able to confirm their own email.
        """
        token = create_jwt_for_user(self.learner)
        response = self.client.post(self._url(self.learner.username), HTTP_AUTHORIZATION=f'JWT {token}')
        assert response.status_code == 403
        self._refresh()
        assert not self.learner.is_active
        assert self.registration.activation_timestamp is None

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.post(self._url(self.learner.username))
        assert response.status_code == 302
        self._refresh()
        assert not self.learner.is_active

    def test_get_not_allowed(self):
        self._login(self.support_staff)
        response = self.client.get(self._url(self.learner.username))
        assert response.status_code == 405
        self._refresh()
        assert not self.learner.is_active

    def test_accounts_api_still_hides_activation_key(self):
        """Guard for the core security fix: the account owner's API response has no activation key."""
        self._login(self.learner)
        response = self.client.get(f'/api/user/v1/accounts/{self.learner.username}')
        assert 'activation_key' not in response.content.decode()
        assert self.registration.activation_key not in response.content.decode()
