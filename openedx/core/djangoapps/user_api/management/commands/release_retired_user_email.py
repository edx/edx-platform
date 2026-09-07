"""
Releases a retired learner's email address so it can be reused for a new
registration, without touching the archived UserRetirementStatus row.
"""
import logging

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.management.base import BaseCommand, CommandError

from openedx.core.djangoapps.user_api.accounts.utils import release_retired_learner_email
from openedx.core.djangoapps.user_api.models import RetirementStateError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Releases a single retired learner's original email address for reuse.

    The learner must already be fully retired (UserRetirementStatus is in the
    COMPLETE state) - see release_retired_learner_email() for the exact rules.
    """
    help = "Releases a retired learner's email address so it can be reused for a new registration."

    def add_arguments(self, parser):
        # Mutually exclusive + required enforces "exactly one of the two" for us,
        # so handle() doesn't need to re-validate that itself.
        identifier = parser.add_mutually_exclusive_group(required=True)
        identifier.add_argument('--username', type=str, help='Username of the retired learner to release.')
        identifier.add_argument('--user_id', type=int, help='User ID of the retired learner to release.')

    def handle(self, *args, **options):
        username = options['username']
        user_id = options['user_id']

        # Branch on which option was actually supplied (argparse leaves the other as
        # None), not on truthiness - an explicitly passed empty --username must still
        # be looked up as itself rather than silently falling through to user_id=None.
        lookup = {'username': username} if username is not None else {'id': user_id}
        try:
            user = User.objects.get(**lookup)
        except User.DoesNotExist as exc:
            # Don't echo the username (PII) back in the error - only user_id is safe to log/print.
            identifier = 'username' if username is not None else f'user_id={user_id!r}'
            raise CommandError(f'No user found for the given {identifier}.') from exc

        try:
            release_retired_learner_email(user)
        except RetirementStateError as exc:
            raise CommandError(str(exc)) from exc

        message = f'Successfully released email for user {user.id}.'
        logger.info(message)
        self.stdout.write(self.style.SUCCESS(message))
