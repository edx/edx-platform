"""
Test the release_retired_user_email management command
"""


import pytest
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.management import CommandError, call_command

from openedx.core.djangoapps.user_api.accounts.tests.retirement_helpers import (  # pylint: disable=unused-import
    create_retirement_status,
    setup_retirement_states
)
from openedx.core.djangoapps.user_api.models import RetirementState
from common.djangoapps.student.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _retire_user(user, state_name):
    return create_retirement_status(user, state=RetirementState.objects.get(state_name=state_name))


def test_releases_email_by_username(setup_retirement_states, capsys):  # pylint: disable=redefined-outer-name, unused-argument
    user = UserFactory(email='retired__user_abc123@retired.invalid')
    _retire_user(user, 'COMPLETE')

    call_command('release_retired_user_email', username=user.username)

    user.refresh_from_db()
    assert user.email == f'retired__uid_{user.id}@{settings.RETIRED_EMAIL_DOMAIN}'
    assert 'Successfully released email' in capsys.readouterr().out


def test_releases_email_by_user_id(setup_retirement_states):  # pylint: disable=redefined-outer-name, unused-argument
    user = UserFactory(email='retired__user_abc123@retired.invalid')
    _retire_user(user, 'COMPLETE')

    call_command('release_retired_user_email', user_id=user.id)

    user.refresh_from_db()
    assert user.email == f'retired__uid_{user.id}@{settings.RETIRED_EMAIL_DOMAIN}'


def test_requires_exactly_one_identifier():
    with pytest.raises(CommandError, match=r'one of the arguments --username --user_id is required'):
        call_command('release_retired_user_email')

    with pytest.raises(CommandError, match=r'not allowed with argument'):
        call_command('release_retired_user_email', username='someone', user_id=1)


def test_unknown_user():
    with pytest.raises(CommandError, match=r'No user found'):
        call_command('release_retired_user_email', username='nonexistent')


def test_blocked_while_retirement_in_progress(setup_retirement_states):  # pylint: disable=redefined-outer-name, unused-argument
    user = UserFactory(email='retired__user_abc123@retired.invalid')
    _retire_user(user, 'RETIRING_LMS')

    with pytest.raises(CommandError, match=r'not COMPLETE'):
        call_command('release_retired_user_email', username=user.username)

    user.refresh_from_db()
    assert User.objects.get(id=user.id).email == 'retired__user_abc123@retired.invalid'


def test_releases_email_when_status_row_archived():
    """
    No UserRetirementStatus row exists for this user (e.g. it was redacted and
    deleted by the partner-report cleanup endpoint), but the email is still in
    the retired-domain format - the command should fall back to that and succeed.
    """
    user = UserFactory(email=f'retired__user_abc123@{settings.RETIRED_EMAIL_DOMAIN}')

    call_command('release_retired_user_email', username=user.username)

    user.refresh_from_db()
    assert user.email == f'retired__uid_{user.id}@{settings.RETIRED_EMAIL_DOMAIN}'


def test_raises_when_user_does_not_appear_retired():
    """
    No UserRetirementStatus row and a non-retired-looking email - the command
    should refuse via CommandError rather than release the email.
    """
    user = UserFactory(email='still.active@example.com')

    with pytest.raises(CommandError, match=r'does not appear to be a retired user'):
        call_command('release_retired_user_email', username=user.username)

    user.refresh_from_db()
    assert user.email == 'still.active@example.com'


def test_running_twice_is_idempotent(setup_retirement_states, capsys):  # pylint: disable=redefined-outer-name, unused-argument
    """
    A second run against an already-released user must not error - it should
    hit release_retired_learner_email()'s early return and still report success.
    """
    user = UserFactory(email='retired__user_abc123@retired.invalid')
    _retire_user(user, 'COMPLETE')

    call_command('release_retired_user_email', username=user.username)
    user.refresh_from_db()
    released_email = user.email

    call_command('release_retired_user_email', username=user.username)

    user.refresh_from_db()
    assert user.email == released_email
    assert 'Successfully released email' in capsys.readouterr().out


def test_unknown_user_id():
    """
    Mirrors test_unknown_user, but for the --user_id branch: the error message
    is built from a separate f'user_id={user_id!r}' f-string that the
    username-only test above never exercises.
    """
    with pytest.raises(CommandError, match=r'No user found for the given user_id=999999'):
        call_command('release_retired_user_email', user_id=999999)
