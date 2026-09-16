"""
Views for the language preference v1 API.
"""

from django.conf import settings
from edx_django_utils.cache import TieredCache
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.core.djangoapps.lang_pref import api as language_api
from openedx.core.djangoapps.lang_pref.rest_api.v1.serializers import SiteLanguageSerializer

# The cache key carries the DarkLangConfig change date, so a configuration change
# invalidates the entry immediately. This timeout is only a backstop for entries left
# behind by something the key does not cover, such as an edit to settings.LANGUAGES.
DEFAULT_RELEASED_LANGUAGES_CACHE_TIMEOUT = 60 * 60  # one hour


class ReleasedLanguagesView(APIView):
    """
    Read-only list of the site languages that are available for selection.

    **Example Request**

        GET /api/lang_pref/v1/released_languages

    **Example Response**

        HTTP 200 OK
        [
            {"code": "en", "name": "English", "released": true},
            {"code": "es-419", "name": "Español (Latinoamérica)", "released": true},
            {"code": "lt-lt", "name": "Lietuvių (Lietuva)", "released": false}
        ]

    Every language in the response is available for selection. ``released`` is ``false``
    for beta languages, which consumers are expected to label or leave out of the picker.

    The response is the same for every caller and holds no user data, so the endpoint is
    unauthenticated: the language picker is rendered for logged-out users too.
    """
    authentication_classes = ()
    permission_classes = (AllowAny,)

    def get(self, request):  # pylint: disable=unused-argument
        """
        Return the list of site languages available for selection.
        """
        cache_key = f'lang_pref.v1.released_languages.{language_api.site_languages_cache_version()}'

        cached_response = TieredCache.get_cached_response(cache_key)
        if cached_response.is_found:
            return Response(cached_response.value)

        # list() so that a plain list of dicts is cached rather than a DRF ReturnList,
        # which holds a reference back to the serializer.
        languages = list(SiteLanguageSerializer(language_api.site_languages(), many=True).data)
        TieredCache.set_all_tiers(
            cache_key,
            languages,
            getattr(
                settings,
                'RELEASED_LANGUAGES_CACHE_TIMEOUT',
                DEFAULT_RELEASED_LANGUAGES_CACHE_TIMEOUT,
            ),
        )
        return Response(languages)
