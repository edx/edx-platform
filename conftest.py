"""  # lint-amnesty, pylint: disable=django-not-configured
Default unit test configuration and fixtures.
"""

from unittest import TestCase

import pytest

# Import hooks and fixture overrides from the cms package to
# avoid duplicating the implementation

from cms.conftest import _django_clear_site_cache, pytest_configure  # pylint: disable=unused-import
from django.test import TransactionTestCase
from django.conf import settings
from oauth2_provider.models import Application

# When using self.assertEquals, diffs are truncated. We don't want that, always
# show the whole diff.
TestCase.maxDiff = None


@pytest.fixture(autouse=True)
def create_edxnotes_oauth_client(request):
    """
    Create edx-notes OAuth2 Application for tests that have DB access
    when ENABLE_EDXNOTES is enabled.
    """
    # Only run for Django class-based tests with DB access.
    if not request.cls:
        return

   

    if not issubclass(request.cls, TransactionTestCase):
        return

    # Skip for edxnotes app tests; they create their own Application.
    if request.node.module.__name__.startswith('lms.djangoapps.edxnotes.'):
        return



    client_name = getattr(settings, 'EDXNOTES_CLIENT_NAME', None)
    if not client_name:
        return

    if not getattr(settings, 'FEATURES', {}).get('ENABLE_EDXNOTES', False):
        return

 

    Application.objects.get_or_create(
        name=client_name,
        defaults={
            'user': None,
            'client_type': Application.CLIENT_CONFIDENTIAL,
            'authorization_grant_type': Application.GRANT_CLIENT_CREDENTIALS,
        },
    )
