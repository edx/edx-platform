"""
Contains all the URLs
"""


from django.conf import settings
from django.urls import path, re_path

from openedx.core.djangoapps.courseware_api import views

urlpatterns = [
    re_path(fr'^course/{settings.COURSE_KEY_PATTERN}',
            views.CoursewareInformation.as_view(),
            name="courseware-api"),
    re_path(fr'^sequence/{settings.USAGE_KEY_PATTERN}',
            views.SequenceMetadata.as_view(),
            name="sequence-api"),
    re_path(fr'^resume/{settings.COURSE_KEY_PATTERN}',
            views.Resume.as_view(),
            name="resume-api"),
    re_path(fr'^celebration/{settings.COURSE_KEY_PATTERN}',
            views.Celebration.as_view(),
            name="celebration-api"),
    # Phase B1: batch child render for large library_content / item_bank verticals
    path('v1/xblock_children/',
         views.XBlockChildren.as_view(),
         name="courseware-xblock-children"),
]

if getattr(settings, 'PROVIDER_STATES_URL', None):
    from .tests.pacts.views import provider_state
    urlpatterns.append(path('pact/provider_states/', provider_state,
                            name='provider-state-view'
                            ))
