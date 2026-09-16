"""
Tests for the language preference v1 API views.
"""

from unittest.mock import patch

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.test.utils import override_settings
from django.urls import reverse
from edx_django_utils.cache import RequestCache

from openedx.core.djangoapps.dark_lang.models import DarkLangConfig
from openedx.core.djangoapps.lang_pref import api as language_api
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms

EN = ('en', 'English')
ES_419 = ('es-419', 'Español (Latinoamérica)')
LT_LT = ('lt-lt', 'Lietuvių (Lietuva)')


@skip_unless_lms
class ReleasedLanguagesViewTest(CacheIsolationTestCase):
    """
    Tests for the released site languages endpoint.

    The endpoint itself is only wired up in ``lms/urls.py``, not ``cms/urls.py``, so these
    tests are skipped when this app's test suite runs under CMS settings (as CI does, since
    ``lang_pref`` is installed in both LMS and CMS) to avoid a ``NoReverseMatch`` on the
    ``lang_pref_api`` namespace, which only exists in the LMS URLconf.
    """
    ENABLED_CACHES = ['default']

    def setUp(self):
        super().setUp()
        self.url = reverse('lang_pref_api:v1:released_languages')
        self.user = User.objects.create(username='dark-lang-admin')
        RequestCache.clear_all_namespaces()

    def _set_dark_lang_config(self, **kwargs):
        """
        Save a new enabled DarkLangConfig and clear the per-request caches behind it.
        """
        DarkLangConfig(changed_by=self.user, enabled=True, **kwargs).save()
        RequestCache.clear_all_namespaces()

    def test_url_matches_the_documented_path(self):
        assert self.url == '/api/lang_pref/v1/released_languages'

    @override_settings(LANGUAGES=[EN, ES_419, LT_LT], LANGUAGE_CODE='en')
    def test_returns_released_and_beta_languages(self):
        self._set_dark_lang_config(
            released_languages='es-419',
            beta_languages='lt-lt',
            enable_beta_languages=True,
        )

        response = self.client.get(self.url)

        assert response.status_code == 200
        assert response.json() == [
            {'code': 'en', 'name': 'English', 'released': True},
            {'code': 'es-419', 'name': 'Español (Latinoamérica)', 'released': True},
            {'code': 'lt-lt', 'name': 'Lietuvių (Lietuva)', 'released': False},
        ]

    @override_settings(LANGUAGES=[EN, ES_419, LT_LT], LANGUAGE_CODE='en')
    def test_beta_languages_are_omitted_when_disabled(self):
        self._set_dark_lang_config(
            released_languages='es-419',
            beta_languages='lt-lt',
            enable_beta_languages=False,
        )

        response = self.client.get(self.url)

        assert response.status_code == 200
        assert [language['code'] for language in response.json()] == ['en', 'es-419']

    @override_settings(LANGUAGES=[EN, ES_419], LANGUAGE_CODE='en')
    def test_available_to_anonymous_users(self):
        self._set_dark_lang_config(released_languages='es-419')

        response = self.client.get(self.url)

        assert response.status_code == 200
        assert len(response.json()) == 2

    @override_settings(LANGUAGES=[EN, ES_419, LT_LT], LANGUAGE_CODE='en')
    def test_response_is_cached(self):
        self._set_dark_lang_config(released_languages='es-419')

        # site_languages() is what does the (relatively) expensive work of computing the
        # response: assembling it from settings.LANGUAGES and DarkLangConfig. Asserting it
        # runs only once demonstrates that the second request is served from cache, without
        # coupling the test to the query count of unrelated middleware (for example,
        # UserStandingMiddleware queries the database on every request, cached or not).
        with patch.object(language_api, 'site_languages', wraps=language_api.site_languages) as mock_site_languages:
            first = self.client.get(self.url)
            second = self.client.get(self.url)

        mock_site_languages.assert_called_once()
        assert first.json() == second.json()

    @override_settings(LANGUAGES=[EN, ES_419, LT_LT], LANGUAGE_CODE='en')
    def test_cache_is_invalidated_by_a_config_change(self):
        self._set_dark_lang_config(released_languages='es-419')
        assert [language['code'] for language in self.client.get(self.url).json()] == ['en', 'es-419']

        self._set_dark_lang_config(
            released_languages='es-419',
            beta_languages='lt-lt',
            enable_beta_languages=True,
        )

        assert [language['code'] for language in self.client.get(self.url).json()] == ['en', 'es-419', 'lt-lt']
