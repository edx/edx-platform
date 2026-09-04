"""
Test the free_retired_user_email management command
"""


import pytest
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


def test_frees_email_by_username(setup_retirement_states, capsys):  # pylint: disable=redefined-outer-name, unused-argument
    user = UserFactory(email='retired__user_abc123@retired.invalid')
    _retire_user(user, 'COMPLETE')

    call_command('free_retired_user_email', username=user.username)

    user.refresh_from_db()
    assert user.email == f'retired__user_abc123@retired.invalid.freed.{user.id}'
    assert 'Successfully freed email' in capsys.readouterr().out


def test_frees_email_by_user_id(setup_retirement_states):  # pylint: disable=redefined-outer-name, unused-argument
    user = UserFactory(email='retired__user_abc123@retired.invalid')
    _retire_user(user, 'COMPLETE')

    call_command('free_retired_user_email', user_id=user.id)

    user.refresh_from_db()
    assert user.email.endswith(f'.freed.{user.id}')


def test_requires_exactly_one_identifier():
    with pytest.raises(CommandError, match=r'exactly one of --username or --user_id'):
        call_command('free_retired_user_email')

    with pytest.raises(CommandError, match=r'exactly one of --username or --user_id'):
        call_command('free_retired_user_email', username='someone', user_id=1)


def test_unknown_user():
    with pytest.raises(CommandError, match=r'No user found'):
        call_command('free_retired_user_email', username='nonexistent')


def test_blocked_while_retirement_in_progress(setup_retirement_states):  # pylint: disable=redefined-outer-name, unused-argument
    user = UserFactory(email='retired__user_abc123@retired.invalid')
    _retire_user(user, 'RETIRING_LMS')

    with pytest.raises(CommandError, match=r'not COMPLETE'):
        call_command('free_retired_user_email', username=user.username)

    user.refresh_from_db()
    assert User.objects.get(id=user.id).email == 'retired__user_abc123@retired.invalid'
