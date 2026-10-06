"""
URL definitions for the language preference API.
"""

from django.urls import include, path

app_name = 'openedx.core.djangoapps.lang_pref.rest_api'

urlpatterns = [
    path('v1/', include('openedx.core.djangoapps.lang_pref.rest_api.v1.urls', namespace='v1')),
]
