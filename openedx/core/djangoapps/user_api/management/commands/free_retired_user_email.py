"""
Frees a retired learner's email address so it can be reused for a new
registration, without touching the archived UserRetirementStatus row.
"""
import logging

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.management.base import BaseCommand, CommandError

from openedx.core.djangoapps.user_api.accounts.utils import free_retired_learner_email
from openedx.core.djangoapps.user_api.models import RetirementStateError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Implementation of the free_retired_user_email command.
    """
    help = "Frees a retired learner's email address so it can be reused for a new registration."

    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, help='Username of the retired learner to free.')
        parser.add_argument('--user_id', type=int, help='User ID of the retired learner to free.')

    def handle(self, *args, **options):
        username = options['username']
        user_id = options['user_id']

        if bool(username) == bool(user_id):
            raise CommandError('Please provide exactly one of --username or --user_id.')

        try:
            user = User.objects.get(username=username) if username else User.objects.get(id=user_id)
        except User.DoesNotExist:
            raise CommandError(f'No user found for username={username!r} user_id={user_id!r}.')  # lint-amnesty, pylint: disable=raise-missing-from

        try:
            free_retired_learner_email(user)
        except RetirementStateError as exc:
            raise CommandError(str(exc))  # lint-amnesty, pylint: disable=raise-missing-from

        logger.info(f'Successfully freed email for user {user.id}.')
        print(f'Successfully freed email for user {user.id}.')
