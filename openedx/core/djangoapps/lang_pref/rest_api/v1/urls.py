"""
URL definitions for the language preference v1 API.
"""

from django.urls import re_path

from openedx.core.djangoapps.lang_pref.rest_api.v1 import views

app_name = 'v1'

urlpatterns = [
    # The trailing slash is optional so that consumers can request either form. APPEND_SLASH
    # only adds a slash, it never strips one, so without this a client that appends a slash
    # out of habit would get a 404.
    re_path(
        r'^released_languages/?$',
        views.ReleasedLanguagesView.as_view(),
        name='released_languages',
    ),
]
